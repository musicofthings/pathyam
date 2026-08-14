"""Quality-control gates, following FAO/INFOODS *Guidelines for Checking Food
Composition Data*.

These run on every compute call. They are cheap, and they catch the failure mode
that matters most: a result that is well-formed, confidently presented, and wrong.
An off-by-one in a quantity expression does not raise - it produces a number, and
only an internal-consistency check will notice.

Statuses
--------
``PASS`` - checked and consistent.
``WARN`` - outside the expected band but not impossible; surface, do not block.
``FAIL`` - internally inconsistent; the result should not be shown to a clinician.
``SKIP`` - the nutrients this gate needs are not present in the dataset.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

__all__ = ["QCResult", "run_all", "GATES"]

# FAO general energy conversion factors (kcal/g).
ATWATER = {"PROCNT": 4.0, "FAT": 9.0, "CHOAVLDF": 4.0, "FIBTG": 2.0, "ALC": 7.0}


@dataclass(frozen=True)
class QCResult:
    gate: str
    status: str
    message: str
    observed: float | None = None
    expected: str | None = None

    @property
    def ok(self) -> bool:
        return self.status in {"PASS", "SKIP"}

    def as_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate, "status": self.status, "message": self.message,
            "observed": None if self.observed is None else round(self.observed, 4),
            "expected": self.expected,
        }

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"[{self.status}] {self.gate}: {self.message}"


def _p50(result, tag: str) -> float | None:
    n = result.nutrients.get(tag)
    return None if n is None else n.per_100g.p50


# --------------------------------------------------------------- the gates ----


def gate_proximate_sum(result) -> QCResult:
    """Water + protein + fat + carbohydrate + fibre + ash should total ~100 g/100 g."""
    parts = {t: _p50(result, t) for t in ("WATER", "PROCNT", "FAT", "CHOAVLDF", "FIBTG", "ASH")}
    present = {k: v for k, v in parts.items() if v is not None}
    if "WATER" not in present or "ASH" not in present:
        return QCResult(
            "proximate_sum", "SKIP",
            "needs WATER and ASH; not present in the dataset yet "
            "(IFCT reports both - this gate activates once real data loads)",
        )
    total = sum(present.values())
    if 97.0 <= total <= 103.0:
        return QCResult("proximate_sum", "PASS",
                        f"proximates sum to {total:.1f} g/100 g", total, "97-103 g")
    status = "FAIL" if total < 90 or total > 110 else "WARN"
    return QCResult("proximate_sum", status,
                    f"proximates sum to {total:.1f} g/100 g", total, "97-103 g")


def gate_energy_atwater(result) -> QCResult:
    """Stated energy should agree with Atwater-calculated energy within 5%."""
    stated = _p50(result, "ENERC_KCAL")
    if stated is None:
        return QCResult("energy_atwater", "SKIP", "no ENERC_KCAL in result")

    components = {t: _p50(result, t) for t in ATWATER}
    if all(v is None for v in components.values()):
        return QCResult("energy_atwater", "SKIP", "no macronutrients to cross-check against")

    calculated = sum(ATWATER[t] * v for t, v in components.items() if v is not None)
    if calculated <= 0:
        return QCResult("energy_atwater", "SKIP", "calculated energy is zero")

    pct = abs(stated - calculated) / calculated * 100.0
    msg = (f"stated {stated:.0f} kcal vs Atwater {calculated:.0f} kcal "
           f"({pct:.1f}% apart)")
    if pct <= 5.0:
        return QCResult("energy_atwater", "PASS", msg, pct, "<= 5%")
    if pct <= 15.0:
        # Commonly a missing macronutrient rather than a real error - say so, so
        # nobody chases a phantom bug in the recipe.
        missing = [t for t, v in components.items() if v is None]
        extra = f"; missing from the sum: {missing}" if missing else ""
        return QCResult("energy_atwater", "WARN", msg + extra, pct, "<= 5%")
    return QCResult("energy_atwater", "FAIL", msg, pct, "<= 5%")


def gate_fatty_acids_le_fat(result) -> QCResult:
    """Summed fatty acids cannot exceed total fat (typically 0.90-0.96x for triglycerides)."""
    fat = _p50(result, "FAT")
    fractions = [_p50(result, t) for t in ("FASAT", "FAMS", "FAPU")]
    present = [v for v in fractions if v is not None]
    if fat is None or not present:
        return QCResult("fatty_acids_le_fat", "SKIP", "needs FAT and at least one fraction")
    total = sum(present)
    if fat == 0:
        return QCResult("fatty_acids_le_fat", "PASS" if total == 0 else "FAIL",
                        f"fat is 0 g but fatty acids sum to {total:.2f} g", total, "<= FAT")
    ratio = total / fat
    if ratio <= 1.0:
        return QCResult("fatty_acids_le_fat", "PASS",
                        f"fatty acids are {ratio:.2f}x total fat", ratio, "<= 1.0")
    status = "WARN" if ratio <= 1.05 else "FAIL"
    return QCResult("fatty_acids_le_fat", status,
                    f"fatty acids are {ratio:.2f}x total fat", ratio, "<= 1.0")


def gate_sugars_le_carbohydrate(result) -> QCResult:
    carb = _p50(result, "CHOAVLDF")
    sugars = _p50(result, "SUGAR")
    if carb is None or sugars is None:
        return QCResult("sugars_le_carbohydrate", "SKIP", "needs CHOAVLDF and SUGAR")
    if sugars <= carb * 1.02:
        return QCResult("sugars_le_carbohydrate", "PASS",
                        f"sugars {sugars:.1f} g <= carbohydrate {carb:.1f} g")
    return QCResult("sugars_le_carbohydrate", "FAIL",
                    f"sugars {sugars:.1f} g exceed carbohydrate {carb:.1f} g")


def gate_yield_plausible(result) -> QCResult:
    """Cooked mass should be a plausible fraction or multiple of raw mass."""
    raw, cooked = result.raw_mass_g.p50, result.cooked_mass_g.p50
    if raw <= 0:
        return QCResult("yield_plausible", "FAIL", "raw mass is zero", raw)
    ratio = cooked / raw
    if 0.2 <= ratio <= 3.0:
        return QCResult("yield_plausible", "PASS",
                        f"cooked/raw = {ratio:.2f}", ratio, "0.2-3.0")
    return QCResult("yield_plausible", "FAIL",
                    f"cooked/raw = {ratio:.2f} is physically implausible", ratio, "0.2-3.0")


def gate_interval_ordering(result) -> QCResult:
    """p10 <= p50 <= p90 for every nutrient. A violation means the Monte Carlo is broken."""
    bad = []
    for tag, n in result.nutrients.items():
        for label, p in (("per_serving", n.per_serving), ("per_100g", n.per_100g)):
            if not (p.p10 <= p.p50 <= p.p90):
                bad.append(f"{tag}.{label}")
    if bad:
        return QCResult("interval_ordering", "FAIL",
                        "percentiles out of order for: " + ", ".join(bad))
    return QCResult("interval_ordering", "PASS",
                    f"percentiles ordered for all {len(result.nutrients)} nutrients")


def gate_non_negative(result) -> QCResult:
    negative = [t for t, n in result.nutrients.items() if n.per_serving.p10 < 0]
    if negative:
        return QCResult("non_negative", "FAIL",
                        "negative nutrient values: " + ", ".join(sorted(negative)))
    return QCResult("non_negative", "PASS", "no negative nutrient values")


def gate_interval_width(result) -> QCResult:
    """Flag an energy interval so wide the estimate carries little information.

    Not a failure - a wide interval on a photo log is honest. But above ~150%
    relative width the app should be asking a question rather than showing a number.
    """
    energy = result.nutrients.get("ENERC_KCAL")
    if energy is None:
        return QCResult("interval_width", "SKIP", "no ENERC_KCAL in result")
    width = energy.per_serving.relative_width
    if width <= 1.5:
        return QCResult("interval_width", "PASS",
                        f"energy 80% CI is {width * 100:.0f}% of the median", width, "<= 150%")
    return QCResult("interval_width", "WARN",
                    f"energy 80% CI is {width * 100:.0f}% of the median - elicit "
                    f"'{result.dominant_uncertainty_param}' before showing a figure",
                    width, "<= 150%")


def gate_composition_coverage(result) -> QCResult:
    """How much of the dish's raw mass has no composition data at all?

    A dish computed from four of its fifteen ingredients still returns a number, and
    that number renders exactly like a complete one. This is the gate that stops a
    30 kcal sambar — which is really "the four ingredients we have values for" —
    from being shown as a nutrition estimate.
    """
    share = getattr(result, "mass_without_composition", 0.0) or 0.0
    if share <= 0.02:
        return QCResult("composition_coverage", "PASS",
                        f"{(1 - share) * 100:.0f}% of raw mass has composition data",
                        share, "<= 2% missing")
    status = "FAIL" if share > 0.20 else "WARN"
    return QCResult("composition_coverage", status,
                    f"{share:.0%} of raw mass has NO composition data — this is an "
                    f"underestimate, not an estimate", share, "<= 2% missing")


def gate_confidence_floor(result) -> QCResult:
    """Tier C/D values are borrowed or imputed; clinical surfaces should suppress them."""
    if result.worst_confidence in {"A", "B"}:
        return QCResult("confidence_floor", "PASS",
                        f"all values are tier {result.worst_confidence} or better")
    weak = sorted(t for t, n in result.nutrients.items()
                  if n.worst_confidence in {"C", "D"})
    return QCResult("confidence_floor", "WARN",
                    f"tier-{result.worst_confidence} values present in: "
                    + ", ".join(weak) + " - suppress these in clinical mode")


GATES: tuple[Callable[[Any], QCResult], ...] = (
    gate_proximate_sum,
    gate_energy_atwater,
    gate_fatty_acids_le_fat,
    gate_sugars_le_carbohydrate,
    gate_yield_plausible,
    gate_composition_coverage,
    gate_interval_ordering,
    gate_non_negative,
    gate_interval_width,
    gate_confidence_floor,
)


def run_all(result) -> list[QCResult]:
    """Run every gate. Gates never raise - a broken gate must not break a compute."""
    out = []
    for gate in GATES:
        try:
            out.append(gate(result))
        except Exception as exc:  # pragma: no cover - defensive
            out.append(QCResult(gate.__name__, "FAIL", f"gate raised: {exc!r}"))
    return out
