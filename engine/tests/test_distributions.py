"""Prior sampling: distribution semantics, truncation, and input validation."""

from __future__ import annotations

import numpy as np
import pytest

from pathyam_engine.distributions import Prior, PriorError, sample_prior

N = 20_000


@pytest.fixture
def rng():
    return np.random.default_rng(20260812)


def test_point_numeric(rng):
    s = sample_prior(Prior("point", {"value": 8.5}), 50, rng)
    assert s.shape == (50,) and np.all(s == 8.5)


def test_point_string(rng):
    s = sample_prior(Prior("point", {"value": "ghee"}), 50, rng)
    assert s.shape == (50,) and set(s) == {"ghee"}


def test_normal_moments(rng):
    s = sample_prior(Prior("normal", {"mu": 12.0, "sigma": 3.0}), N, rng)
    assert abs(s.mean() - 12.0) < 0.1
    assert abs(s.std() - 3.0) < 0.1


def test_lognormal_uses_underlying_normal_parameters(rng):
    """mu/sigma describe ln(X), matching the schema comment.

    Getting this backwards is a classic silent 2-3x error in portion sizes, so it
    is worth pinning: median of a lognormal is exp(mu).
    """
    mu, sigma = 2.08, 0.55
    s = sample_prior(Prior("lognormal", {"mu": mu, "sigma": sigma}), N, rng)
    assert abs(np.median(s) - np.exp(mu)) < 0.15
    assert abs(np.log(s).mean() - mu) < 0.02
    assert abs(np.log(s).std() - sigma) < 0.02


def test_uniform_bounds(rng):
    s = sample_prior(Prior("uniform", {"lo": 4.0, "hi": 12.0}), N, rng)
    assert s.min() >= 4.0 and s.max() <= 12.0


def test_beta_in_unit_interval(rng):
    s = sample_prior(Prior("beta", {"alpha": 2.0, "beta": 5.0}), N, rng)
    assert s.min() >= 0.0 and s.max() <= 1.0
    assert abs(s.mean() - 2 / 7) < 0.01


def test_categorical_respects_weights(rng):
    prior = Prior("categorical", {
        "categories": ["gingelly", "coconut", "ghee", "sunflower"],
        "weights": [0.45, 0.30, 0.15, 0.10],
    })
    s = sample_prior(prior, N, rng)
    freq = {c: float((s == c).mean()) for c in ["gingelly", "coconut", "ghee", "sunflower"]}
    assert abs(freq["gingelly"] - 0.45) < 0.02
    assert abs(freq["coconut"] - 0.30) < 0.02
    assert abs(freq["ghee"] - 0.15) < 0.02


def test_categorical_normalises_unnormalised_weights(rng):
    s = sample_prior(Prior("categorical", {"categories": ["a", "b"], "weights": [3, 1]}), N, rng)
    assert abs((s == "a").mean() - 0.75) < 0.02


# ------------------------------------------------------------- truncation ----

def test_mass_units_get_implicit_non_negative_bound(rng):
    """A normal prior on grams must not produce negative mass."""
    prior = Prior.from_row("normal", {"mu": 4.0, "sigma": 4.0}, unit="g", dtype="continuous")
    assert prior.lo == 0.0
    s = sample_prior(prior, N, rng)
    assert s.min() >= 0.0


def test_truncation_resamples_rather_than_clipping(rng):
    """Clipping would pile mass exactly on the bound; resampling must not."""
    prior = Prior.from_row("normal", {"mu": 4.0, "sigma": 4.0}, unit="g", dtype="continuous")
    s = sample_prior(prior, N, rng)
    at_bound = float((s == 0.0).mean())
    assert at_bound < 0.001, f"{at_bound:.4f} of draws sit exactly on the bound"
    assert not prior.warnings


def test_hopeless_bounds_clip_and_warn(rng):
    """A prior badly matched to its bounds should still return - and say so."""
    prior = Prior("normal", {"mu": -100.0, "sigma": 1.0}, lo=0.0)
    s = sample_prior(prior, 200, rng)
    assert np.all(s >= 0.0)
    assert prior.warnings and "mis-specified" in prior.warnings[0]


def test_ratio_dtype_bounded_to_unit_interval():
    prior = Prior.from_row("normal", {"mu": 0.75, "sigma": 0.04},
                           unit="ratio", dtype="continuous")
    assert prior.lo == 0.0 and prior.hi == 1.0


def test_explicit_bounds_survive_from_row():
    prior = Prior.from_row("normal", {"mu": 5, "sigma": 2, "lo": 1, "hi": 9}, unit="g")
    assert prior.lo == 1 and prior.hi == 9


# ------------------------------------------------------------- validation ----

@pytest.mark.parametrize("dist,params,match", [
    ("normal", {"mu": 1}, "missing required key"),
    ("lognormal", {"sigma": 1}, "missing required key"),
    ("uniform", {"lo": 1}, "missing required key"),
    ("beta", {"alpha": 1}, "missing required key"),
    ("point", {}, "missing required key"),
    ("categorical", {"categories": ["a"]}, "missing required key"),
    ("weibull", {"k": 1}, "unknown distribution"),
])
def test_malformed_priors_raise(dist, params, match, rng):
    with pytest.raises(PriorError, match=match):
        sample_prior(Prior(dist, params), 10, rng)


def test_mismatched_categorical_lengths(rng):
    with pytest.raises(PriorError, match="categories but"):
        sample_prior(
            Prior("categorical", {"categories": ["a", "b"], "weights": [1.0]}), 10, rng
        )


def test_negative_sigma_rejected(rng):
    with pytest.raises(PriorError, match="non-negative"):
        sample_prior(Prior("normal", {"mu": 1, "sigma": -1}), 10, rng)


def test_zero_weights_rejected(rng):
    with pytest.raises(PriorError, match="sum to zero"):
        sample_prior(Prior("categorical", {"categories": ["a"], "weights": [0]}), 10, rng)


def test_sampling_is_reproducible():
    p = Prior("lognormal", {"mu": 2.0, "sigma": 0.5})
    a = sample_prior(p, 500, np.random.default_rng(42))
    b = sample_prior(p, 500, np.random.default_rng(42))
    np.testing.assert_array_equal(a, b)
