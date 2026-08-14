"""QC gates. Each test constructs a deliberately broken result and checks it is caught."""

from __future__ import annotations

from dataclasses import replace

import pytest

from pathyam_engine import ComputeEngine
from pathyam_engine.models import NutrientResult, Percentiles
from pathyam_engine import qc


def _flat(value: float) -> Percentiles:
    return Percentiles(p10=value, p50=value, p90=value, mean=value, sd=0.0)


def _nutrient(tag: str, per_serving: float, per_100g: float, tier: str = "A") -> NutrientResult:
    return NutrientResult(tagname=tag, name=tag, unit="g",
                          per_serving=_flat(per_serving), per_100g=_flat(per_100g),
                          worst_confidence=tier)


@pytest.fixture
def result(repo):
    return ComputeEngine(repo).compute(
        "PY-T-000100", n_samples=500,
        param_overrides={"batter_g": 100.0, "rice_fraction": 0.75,
                         "fat_g": 10.0, "fat_type": "gingelly"},
        sample_composition_sd=False,
    )


# --------------------------------------------------------------- happy path ----

def test_all_gates_run(result):
    results = qc.run_all(result)
    assert len(results) == len(qc.GATES)
    assert {r.gate for r in results} >= {
        "energy_atwater", "yield_plausible", "interval_ordering",
        "non_negative", "interval_width", "confidence_floor",
    }


def test_gates_never_raise(result):
    """A broken gate must degrade to FAIL, never take down a compute."""
    broken = lambda r: 1 / 0  # noqa: E731
    broken.__name__ = "broken_gate"
    original = qc.GATES
    try:
        qc.GATES = original + (broken,)
        out = qc.run_all(result)
    finally:
        qc.GATES = original
    assert out[-1].status == "FAIL" and "raised" in out[-1].message


# ------------------------------------------------------------ energy gate ----

def test_energy_atwater_passes_on_consistent_macros(result):
    """Rice + urad + oil should reconcile: the seed values are internally consistent."""
    out = qc.gate_energy_atwater(result)
    assert out.status in {"PASS", "WARN"}
    assert out.observed is not None and out.observed < 15.0


def test_energy_atwater_fails_when_energy_is_wrong(result):
    bad = replace(result)
    bad.nutrients = dict(result.nutrients)
    bad.nutrients["ENERC_KCAL"] = _nutrient("ENERC_KCAL", 9999, 9999)
    out = qc.gate_energy_atwater(bad)
    assert out.status == "FAIL"


def test_energy_atwater_skips_without_energy(result):
    bad = replace(result)
    bad.nutrients = {k: v for k, v in result.nutrients.items() if k != "ENERC_KCAL"}
    assert qc.gate_energy_atwater(bad).status == "SKIP"


# -------------------------------------------------------------- structural ----

def test_interval_ordering_catches_inverted_percentiles(result):
    bad = replace(result)
    bad.nutrients = dict(result.nutrients)
    bad.nutrients["FAT"] = NutrientResult(
        "FAT", "Total fat", "g",
        per_serving=Percentiles(p10=50, p50=10, p90=20, mean=10, sd=1),
        per_100g=_flat(10), worst_confidence="A",
    )
    out = qc.gate_interval_ordering(bad)
    assert out.status == "FAIL" and "FAT" in out.message


def test_non_negative_catches_negative_values(result):
    bad = replace(result)
    bad.nutrients = dict(result.nutrients)
    bad.nutrients["K"] = NutrientResult(
        "K", "Potassium", "mg",
        per_serving=Percentiles(p10=-5, p50=10, p90=20, mean=10, sd=1),
        per_100g=_flat(10), worst_confidence="A",
    )
    assert qc.gate_non_negative(bad).status == "FAIL"


def test_yield_plausible_rejects_absurd_ratios(result):
    bad = replace(result)
    bad.cooked_mass_g = _flat(result.raw_mass_g.p50 * 12)
    out = qc.gate_yield_plausible(bad)
    assert out.status == "FAIL" and "implausible" in out.message


# --------------------------------------------------------- domain-specific ----

def test_fatty_acids_cannot_exceed_total_fat(result):
    bad = replace(result)
    bad.nutrients = dict(result.nutrients)
    bad.nutrients["FAT"] = _nutrient("FAT", 10, 10)
    bad.nutrients["FASAT"] = _nutrient("FASAT", 12, 12)
    out = qc.gate_fatty_acids_le_fat(bad)
    assert out.status == "FAIL" and out.observed == pytest.approx(1.2)


def test_fatty_acids_tolerates_typical_triglyceride_ratio(result):
    ok = replace(result)
    ok.nutrients = dict(result.nutrients)
    ok.nutrients["FAT"] = _nutrient("FAT", 10, 10)
    ok.nutrients["FASAT"] = _nutrient("FASAT", 3, 3)
    ok.nutrients["FAMS"] = _nutrient("FAMS", 4, 4)
    ok.nutrients["FAPU"] = _nutrient("FAPU", 2, 2)
    assert qc.gate_fatty_acids_le_fat(ok).status == "PASS"


def test_sugars_cannot_exceed_carbohydrate(result):
    bad = replace(result)
    bad.nutrients = dict(result.nutrients)
    bad.nutrients["CHOAVLDF"] = _nutrient("CHOAVLDF", 50, 50)
    bad.nutrients["SUGAR"] = _nutrient("SUGAR", 60, 60)
    assert qc.gate_sugars_le_carbohydrate(bad).status == "FAIL"


def test_proximate_sum_skips_without_water_and_ash(result):
    assert qc.gate_proximate_sum(result).status == "SKIP"


def test_proximate_sum_checks_when_data_is_complete(result):
    full = replace(result)
    full.nutrients = dict(result.nutrients)
    full.nutrients["WATER"] = _nutrient("WATER", 60, 60)
    full.nutrients["ASH"] = _nutrient("ASH", 1.5, 1.5)
    full.nutrients["PROCNT"] = _nutrient("PROCNT", 8, 8)
    full.nutrients["FAT"] = _nutrient("FAT", 10, 10)
    full.nutrients["CHOAVLDF"] = _nutrient("CHOAVLDF", 18, 18)
    full.nutrients["FIBTG"] = _nutrient("FIBTG", 2, 2)
    out = qc.gate_proximate_sum(full)
    assert out.status == "PASS" and out.observed == pytest.approx(99.5)


def test_proximate_sum_fails_when_far_off(result):
    bad = replace(result)
    bad.nutrients = dict(result.nutrients)
    bad.nutrients["WATER"] = _nutrient("WATER", 20, 20)
    bad.nutrients["ASH"] = _nutrient("ASH", 1, 1)
    assert qc.gate_proximate_sum(bad).status == "FAIL"


# ------------------------------------------------------------- uncertainty ----

def test_interval_width_warns_when_estimate_is_uninformative(repo):
    """A very wide interval should prompt elicitation rather than a displayed number."""
    wide = ComputeEngine(repo).compute("PY-T-000100", n_samples=3000)
    wide_result = replace(wide)
    wide_result.nutrients = dict(wide.nutrients)
    wide_result.nutrients["ENERC_KCAL"] = NutrientResult(
        "ENERC_KCAL", "Energy", "kcal",
        per_serving=Percentiles(p10=100, p50=300, p90=900, mean=400, sd=200),
        per_100g=_flat(300), worst_confidence="A",
    )
    out = qc.gate_interval_width(wide_result)
    assert out.status == "WARN" and "elicit" in out.message


def test_confidence_floor_flags_borrowed_tiers(result):
    weak = replace(result)
    weak.worst_confidence = "C"
    weak.nutrients = dict(result.nutrients)
    weak.nutrients["K"] = _nutrient("K", 100, 100, tier="C")
    out = qc.gate_confidence_floor(weak)
    assert out.status == "WARN" and "clinical mode" in out.message


def test_qc_result_serialises(result):
    d = qc.run_all(result)[0].as_dict()
    assert set(d) == {"gate", "status", "message", "observed", "expected"}
