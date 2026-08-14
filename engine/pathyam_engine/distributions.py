"""Sampling from the parameter priors stored in ``prior_params`` JSONB.

Distribution families mirror ``ref.prior_dist`` in the schema. The payload shapes
are validated in the database by ``ref.valid_prior_params()``; they are validated
again here, because a compute engine that trusts its inputs is a compute engine
that produces confident nonsense when someone edits a row by hand.

Truncation note
---------------
Physical quantities cannot be negative, but a normal prior can sample below zero.
Clipping to the bound is the obvious fix and it is wrong: it piles probability mass
onto the boundary and biases the mean upward. This module resamples the violating
draws instead, falling back to clipping only for stragglers after a bounded number
of rounds - and it records a warning when that fallback fires, because needing it
means the prior itself is badly specified for the bound.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

import numpy as np

__all__ = ["Prior", "PriorError", "sample_prior"]

_MAX_RESAMPLE_ROUNDS = 24


class PriorError(ValueError):
    """Raised for a malformed or unsamplable prior specification."""


@dataclass(frozen=True)
class Prior:
    """A parameter prior: a distribution family plus its parameters."""

    dist: str
    params: Mapping[str, Any]
    lo: float | None = None
    hi: float | None = None
    warnings: list[str] = field(default_factory=list, compare=False)

    @classmethod
    def from_row(
        cls,
        dist: str,
        params: Mapping[str, Any],
        *,
        unit: str | None = None,
        dtype: str | None = None,
    ) -> "Prior":
        """Build from a database row, inferring a non-negativity bound.

        Mass and volume units get an implicit ``lo=0``: a recipe cannot contain
        -3 g of oil, and letting that through produces a plausible-looking energy
        figure that is quietly too low.
        """
        lo = params.get("lo")
        hi = params.get("hi")
        if lo is None and unit in {"g", "ml", "kg", "l", "mg", "ug", "h", "min"}:
            lo = 0.0
        if lo is None and dtype == "continuous" and unit == "ratio":
            lo, hi = 0.0, (hi if hi is not None else 1.0)
        return cls(dist=dist, params=dict(params), lo=lo, hi=hi)

    def scaled(self, factor: float) -> "Prior":
        """Shift the prior's location by ``factor``, preserving its shape.

        Used to apply the magnitude modifiers the text parser extracts: "konjam
        ennai" (a little oil) scales the ``fat_g`` prior by 0.6 rather than pinning
        it to a made-up number. The user said *less*, not *exactly 4.8 g*, so the
        result stays a distribution.

        For a lognormal this shifts ``mu`` by ``ln(factor)``, which scales the median
        exactly - multiplying ``mu`` itself would be wrong, since ``mu`` lives in log
        space. Categorical and beta priors have no meaningful scaling and pass through.
        """
        if factor <= 0:
            raise PriorError(f"scale factor must be positive, got {factor}")
        if factor == 1.0 or self.dist in {"categorical", "beta"}:
            return self

        p = dict(self.params)
        if self.dist == "point":
            value = p["value"]
            if isinstance(value, str):
                return self
            p["value"] = float(value) * factor
        elif self.dist == "lognormal":
            p["mu"] = float(p["mu"]) + float(np.log(factor))
        elif self.dist == "normal":
            p["mu"] = float(p["mu"]) * factor
            p["sigma"] = float(p["sigma"]) * factor
        elif self.dist == "uniform":
            p["lo"] = float(p["lo"]) * factor
            p["hi"] = float(p["hi"]) * factor

        return Prior(
            dist=self.dist, params=p,
            lo=self.lo, hi=None if self.hi is None else self.hi * factor,
        )


def sample_prior(prior: Prior, n: int, rng: np.random.Generator) -> np.ndarray:
    """Draw ``n`` samples. Returns a float array, or a string array for categoricals."""
    if n <= 0:
        raise PriorError(f"n must be positive, got {n}")

    dist = prior.dist
    p = prior.params

    if dist == "point":
        _require(p, ["value"], dist)
        value = p["value"]
        if isinstance(value, str):
            return np.full(n, value, dtype=object)
        if isinstance(value, bool):
            return np.full(n, float(value))
        return np.full(n, float(value))

    if dist == "categorical":
        _require(p, ["categories", "weights"], dist)
        categories = list(p["categories"])
        weights = np.asarray(p["weights"], dtype=float)
        if len(categories) != len(weights):
            raise PriorError(
                f"categorical prior has {len(categories)} categories but "
                f"{len(weights)} weights"
            )
        if np.any(weights < 0):
            raise PriorError("categorical weights must be non-negative")
        total = weights.sum()
        if total <= 0:
            raise PriorError("categorical weights sum to zero")
        return rng.choice(np.asarray(categories, dtype=object), size=n, p=weights / total)

    if dist == "normal":
        _require(p, ["mu", "sigma"], dist)
        sigma = float(p["sigma"])
        if sigma < 0:
            raise PriorError(f"normal sigma must be non-negative, got {sigma}")
        draw = lambda k: rng.normal(float(p["mu"]), sigma, k)  # noqa: E731

    elif dist == "lognormal":
        # NumPy's lognormal takes the mean and sigma of the UNDERLYING normal,
        # which matches the schema comment ("mu, sigma of ln X"). Getting this
        # backwards is a classic silent 2-3x error in portion sizes.
        _require(p, ["mu", "sigma"], dist)
        sigma = float(p["sigma"])
        if sigma < 0:
            raise PriorError(f"lognormal sigma must be non-negative, got {sigma}")
        draw = lambda k: rng.lognormal(float(p["mu"]), sigma, k)  # noqa: E731

    elif dist == "uniform":
        _require(p, ["lo", "hi"], dist)
        lo_v, hi_v = float(p["lo"]), float(p["hi"])
        if hi_v < lo_v:
            raise PriorError(f"uniform hi ({hi_v}) is below lo ({lo_v})")
        draw = lambda k: rng.uniform(lo_v, hi_v, k)  # noqa: E731

    elif dist == "beta":
        _require(p, ["alpha", "beta"], dist)
        a, b = float(p["alpha"]), float(p["beta"])
        if a <= 0 or b <= 0:
            raise PriorError(f"beta alpha and beta must be positive, got {a}, {b}")
        draw = lambda k: rng.beta(a, b, k)  # noqa: E731

    else:
        raise PriorError(f"unknown distribution family {dist!r}")

    return _draw_within_bounds(draw, n, prior)


def _draw_within_bounds(draw, n: int, prior: Prior) -> np.ndarray:
    """Rejection-resample out-of-bounds draws rather than clipping them."""
    lo, hi = prior.lo, prior.hi
    samples = draw(n)
    if lo is None and hi is None:
        return samples

    for _ in range(_MAX_RESAMPLE_ROUNDS):
        bad = np.zeros(n, dtype=bool)
        if lo is not None:
            bad |= samples < lo
        if hi is not None:
            bad |= samples > hi
        count = int(bad.sum())
        if count == 0:
            return samples
        samples[bad] = draw(count)

    # Still violating after many rounds: the prior is badly matched to its bounds
    # (e.g. normal(mu=2, sigma=10) with lo=0). Clip, and say so.
    remaining = int(
        ((samples < lo) if lo is not None else np.zeros(n, bool)).sum()
        + ((samples > hi) if hi is not None else np.zeros(n, bool)).sum()
    )
    prior.warnings.append(
        f"{remaining}/{n} draws from {prior.dist} still outside bounds "
        f"[{lo}, {hi}] after {_MAX_RESAMPLE_ROUNDS} resample rounds; clipped. "
        f"The prior is likely mis-specified for its bounds."
    )
    return np.clip(samples, lo if lo is not None else -np.inf,
                   hi if hi is not None else np.inf)


def _require(params: Mapping[str, Any], keys: list[str], dist: str) -> None:
    missing = [k for k in keys if k not in params]
    if missing:
        raise PriorError(
            f"{dist} prior missing required key(s) {missing}; got {sorted(params)}"
        )
