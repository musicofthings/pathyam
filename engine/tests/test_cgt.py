"""The illustrative glucose curve, and the labelling that must travel with it."""

from __future__ import annotations

import pytest

from pathyam_engine import cgt


def test_glycemic_load_follows_the_standard_definition():
    """GL = available carbohydrate x GI / 100. The one part that is citable."""
    assert cgt.glycemic_load(carbs_g=50.0, gi=70.0) == pytest.approx(35.0)
    assert cgt.glycemic_load(carbs_g=50.0, gi=70.0, servings=2.0) == pytest.approx(70.0)


def test_the_curve_is_never_marked_validated():
    """It has never been fitted to or tested against real CGM data."""
    curve = cgt.predict_curve(carbs_g=45.0, gi=68.0)
    assert curve.is_validated is False
    assert "not a validated clinical model" in curve.disclaimer.lower()
    assert curve.model_id == "illustrative-v1"


def test_the_disclaimer_survives_serialisation():
    """A client must not be able to render the curve without receiving the caveat."""
    payload = cgt.predict_curve(carbs_g=45.0, gi=68.0).as_dict()
    assert payload["is_validated"] is False
    assert payload["disclaimer"]
    assert payload["model_id"]


# ----------------------------------------------------- directional behaviour ----
#
# These pin the DIRECTION of each term, which is what the literature supports. They
# deliberately assert no magnitude: the coefficients are unsourced tuning, and a test
# fixing them to a value would dress a guess up as a specification.


def test_more_carbohydrate_raises_the_peak():
    low = cgt.predict_curve(carbs_g=20.0, gi=68.0)
    high = cgt.predict_curve(carbs_g=80.0, gi=68.0)
    assert high.peak_mg_dl > low.peak_mg_dl


def test_fat_lowers_the_peak_and_pushes_it_later():
    """Fat delays gastric emptying — established in direction."""
    without = cgt.predict_curve(carbs_g=60.0, gi=68.0, fat_g=0.0)
    with_fat = cgt.predict_curve(carbs_g=60.0, gi=68.0, fat_g=30.0)
    assert with_fat.peak_mg_dl < without.peak_mg_dl
    assert with_fat.time_to_peak_min > without.time_to_peak_min


def test_fibre_lowers_the_peak():
    """Viscous soluble fibre attenuates the postprandial rise."""
    without = cgt.predict_curve(carbs_g=60.0, gi=68.0, fibre_g=0.0)
    with_fibre = cgt.predict_curve(carbs_g=60.0, gi=68.0, fibre_g=15.0)
    assert with_fibre.peak_mg_dl < without.peak_mg_dl


# ------------------------------------------------------------- curve shape ----

def test_the_curve_starts_at_baseline_and_returns_toward_it():
    curve = cgt.predict_curve(carbs_g=60.0, gi=68.0, baseline_mg_dl=95.0)
    assert curve.curve[0].glucose_mg_dl == pytest.approx(95.0)
    assert curve.curve[-1].glucose_mg_dl < curve.peak_mg_dl
    assert curve.curve[-1].glucose_mg_dl > 95.0, "a 3h window does not fully return"


def test_the_peak_value_matches_the_drawn_curve():
    """The headline number and the graph must not disagree."""
    curve = cgt.predict_curve(carbs_g=60.0, gi=68.0, fat_g=10.0)
    drawn_peak = max(p.glucose_mg_dl for p in curve.curve)
    assert drawn_peak == pytest.approx(curve.peak_mg_dl, abs=1.0)

    at_peak = min(curve.curve, key=lambda p: abs(p.time_minutes - curve.time_to_peak_min))
    assert at_peak.glucose_mg_dl == pytest.approx(drawn_peak, abs=1.0)


def test_a_zero_carbohydrate_meal_still_produces_a_floor_not_a_negative():
    curve = cgt.predict_curve(carbs_g=0.0, gi=0.0, baseline_mg_dl=95.0)
    assert curve.glycemic_load == 0.0
    assert curve.peak_mg_dl >= 95.0
    assert curve.iauc_mg_dl_min >= 0.0


def test_iauc_counts_only_area_above_baseline():
    curve = cgt.predict_curve(carbs_g=60.0, gi=68.0)
    assert curve.iauc_mg_dl_min > 0.0
    # Bounded by the rectangle over the whole window at the peak height.
    ceiling = (curve.peak_mg_dl - curve.baseline_mg_dl) * 180
    assert curve.iauc_mg_dl_min < ceiling


def test_the_journal_and_the_cgt_tab_use_one_implementation():
    """They previously carried separate copies of the same four coefficients."""
    from pathyam_api.journal import _illustrative_peak

    expected = cgt.predict_curve(
        carbs_g=50.0, gi=68.0, fibre_g=6.0, fat_g=12.0, baseline_mg_dl=95.0
    ).peak_mg_dl
    assert _illustrative_peak(50.0, 12.0, 6.0) == pytest.approx(expected)
