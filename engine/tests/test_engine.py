"""Engine behaviour: arithmetic, sub-recipe scaling, determinism, sensitivity."""

from __future__ import annotations

import numpy as np
import pytest

from pathyam_engine import ComputeEngine, EngineError, Prior

N = 3000


@pytest.fixture
def engine(repo):
    return ComputeEngine(repo)


# ------------------------------------------------------------- basic maths ----

def test_fully_pinned_parameters_give_exact_arithmetic(engine):
    """With every parameter fixed, the engine reduces to plain arithmetic we can hand-check."""
    r = engine.compute(
        "PY-T-000100", n_samples=200,
        param_overrides={"batter_g": 100.0, "rice_fraction": 0.75,
                         "fat_g": 10.0, "fat_type": "gingelly"},
        sample_composition_sd=False,
    )
    # rice 75 g, urad 25 g, gingelly oil 10 g  => 110 g raw
    assert r.raw_mass_g.p50 == pytest.approx(110.0)
    assert r.cooked_mass_g.p50 == pytest.approx(110.0 * 0.82)

    expected_kcal = 0.75 * 346.0 + 0.25 * 341.0 + 0.10 * 900.0
    assert r.energy.per_serving.p50 == pytest.approx(expected_kcal, rel=1e-9)
    # every parameter pinned => zero spread
    assert r.energy.per_serving.p10 == pytest.approx(r.energy.per_serving.p90)


def test_categorical_selector_routes_mass_to_one_food(engine):
    r = engine.compute(
        "PY-T-000100", n_samples=200,
        param_overrides={"batter_g": 100.0, "rice_fraction": 0.75,
                         "fat_g": 10.0, "fat_type": "ghee"},
        sample_composition_sd=False,
    )
    names = {row["food_name"] for row in r.ingredients}
    assert "Ghee, cow" in names
    assert "Gingelly oil" not in names, "unused selector branch must not appear"


def test_retention_factor_is_applied(engine):
    """Thiamine has a 70%/75% retention factor; energy has none."""
    r = engine.compute(
        "PY-T-000100", n_samples=200,
        param_overrides={"batter_g": 100.0, "rice_fraction": 0.75,
                         "fat_g": 0.0, "fat_type": "gingelly"},
        sample_composition_sd=False,
    )
    expected_thia = 0.75 * 0.28 * 0.70 + 0.25 * 0.25 * 0.75
    assert r.nutrients["THIA"].per_serving.p50 == pytest.approx(expected_thia, rel=1e-9)


def test_per_100g_uses_cooked_mass(engine):
    r = engine.compute(
        "PY-T-000100", n_samples=200,
        param_overrides={"batter_g": 100.0, "rice_fraction": 0.75,
                         "fat_g": 10.0, "fat_type": "gingelly"},
        sample_composition_sd=False,
    )
    total = r.energy.per_serving.p50
    assert r.energy.per_100g.p50 == pytest.approx(total / (110.0 * 0.82) * 100.0, rel=1e-9)


def test_servings_divide_the_batch(engine):
    kw = dict(n_samples=200, sample_composition_sd=False,
              param_overrides={"batter_g": 100.0, "rice_fraction": 0.75,
                               "fat_g": 10.0, "fat_type": "gingelly"})
    one = engine.compute("PY-T-000100", servings=1, **kw)
    two = engine.compute("PY-T-000100", servings=2, **kw)
    assert two.energy.per_serving.p50 == pytest.approx(one.energy.per_serving.p50 / 2)
    # per-100g is a property of the food, not of how it is portioned
    assert two.energy.per_100g.p50 == pytest.approx(one.energy.per_100g.p50)


# -------------------------------------------------- nested sub-recipe scaling --

def test_sub_recipe_target_mass_is_normalised(engine):
    """A sub-recipe reference is a TARGET MASS, not a multiplier.

    The filling template's own batch is 40 + 10 + 0 = 50 g. Asking for 100 g of
    filling must scale that batch by 2, giving 80 g potato and 20 g onion - NOT
    100 g of each, and not 40/10 unchanged.
    """
    r = engine.compute(
        "PY-T-000101", n_samples=200,
        param_overrides={
            "batter_g": 100.0, "rice_fraction": 0.75, "fat_g": 0.0,
            "fat_type": "gingelly", "filling_g": 100.0,
            "potato_g": 40.0, "onion_g": 10.0, "masala_fat_g": 0.0,
        },
        sample_composition_sd=False,
    )
    grams = {row["food_name"]: row["grams"]["p50"] for row in r.ingredients}
    assert grams["Potato, boiled"] == pytest.approx(80.0)
    assert grams["Onion, big"] == pytest.approx(20.0)
    # 75 rice + 25 urad + 100 filling
    assert r.raw_mass_g.p50 == pytest.approx(200.0)


def test_sub_recipe_ingredients_are_tagged_with_their_template(engine):
    r = engine.compute("PY-T-000101", n_samples=200)
    vias = {row["food_name"]: row["via_sub_template"] for row in r.ingredients}
    assert vias["Potato, boiled"] == "PY-T-000102"
    assert vias["Rice, parboiled, milled"] is None


def test_sub_recipe_nutrients_scale_correctly(engine):
    r = engine.compute(
        "PY-T-000101", n_samples=200,
        param_overrides={
            "batter_g": 0.0001, "rice_fraction": 0.75, "fat_g": 0.0,
            "fat_type": "gingelly", "filling_g": 100.0,
            "potato_g": 40.0, "onion_g": 10.0, "masala_fat_g": 0.0,
        },
        sample_composition_sd=False,
    )
    # 80 g potato + 20 g onion, batter negligible
    expected = 0.80 * 87.0 + 0.20 * 46.0
    assert r.energy.per_serving.p50 == pytest.approx(expected, rel=1e-3)


# ------------------------------------------------------------ determinism ----

def test_same_inputs_give_identical_output(engine):
    a = engine.compute("PY-T-000101", n_samples=500, region_key="KL")
    b = engine.compute("PY-T-000101", n_samples=500, region_key="KL")
    assert a.seed == b.seed
    assert a.energy.per_serving.as_dict() == b.energy.per_serving.as_dict()
    assert a.as_dict() == b.as_dict()


def test_seed_is_stable_across_processes(engine):
    """Derived from blake2b, not Python's per-process-salted hash()."""
    seed = engine._derive_seed("PY-T-000101", {"fat_g": 11.0}, "KA", 2000)
    assert seed == engine._derive_seed("PY-T-000101", {"fat_g": 11.0}, "KA", 2000)
    assert seed != engine._derive_seed("PY-T-000101", {"fat_g": 12.0}, "KA", 2000)


def test_different_seeds_give_different_draws(engine):
    a = engine.compute("PY-T-000100", n_samples=500, seed=1)
    b = engine.compute("PY-T-000100", n_samples=500, seed=2)
    assert a.energy.per_serving.p50 != b.energy.per_serving.p50


# ------------------------------------------------------------- uncertainty ----

def test_unpinned_compute_produces_a_real_interval(engine):
    r = engine.compute("PY-T-000101", n_samples=N)
    s = r.energy.per_serving
    assert s.p10 < s.p50 < s.p90
    assert 0.1 < s.relative_width < 3.0
    assert "kcal" in r.summary_line() and "80% CI" in r.summary_line()


def test_cooking_fat_dominates_energy_uncertainty(engine):
    """The whole 'ask one question' UX rests on this being detected correctly."""
    r = engine.compute("PY-T-000100", n_samples=N)
    assert r.dominant_uncertainty_param in {"fat_g", "batter_g"}
    assert sum(r.variance_contributions.values()) == pytest.approx(1.0, abs=1e-6)


def test_pinning_the_dominant_parameter_shrinks_the_interval(engine):
    wide = engine.compute("PY-T-000100", n_samples=N, seed=11)
    narrow = engine.compute("PY-T-000100", n_samples=N, seed=11,
                            param_overrides={"fat_g": 8.0})
    assert narrow.energy.per_serving.relative_width < wide.energy.per_serving.relative_width


def test_composition_sd_widens_the_interval(engine):
    kw = dict(n_samples=N, seed=5,
              param_overrides={"batter_g": 100.0, "rice_fraction": 0.75,
                               "fat_g": 10.0, "fat_type": "gingelly"})
    without = engine.compute("PY-T-000100", sample_composition_sd=False, **kw)
    with_sd = engine.compute("PY-T-000100", sample_composition_sd=True, **kw)
    assert without.energy.per_serving.sd == pytest.approx(0.0, abs=1e-9)
    assert with_sd.energy.per_serving.sd > 0.0


# ------------------------------------------------------- priors and regions ----

def test_regional_prior_shifts_the_fat_mix(engine):
    plain = engine.compute("PY-T-000100", n_samples=N, seed=3)
    kerala = engine.compute("PY-T-000100", n_samples=N, seed=3, region_key="KL")
    ghee_plain = plain.parameter_summary["fat_type"]["distribution"].get("ghee", 0)
    ghee_kerala = kerala.parameter_summary["fat_type"]["distribution"].get("ghee", 0)
    assert ghee_kerala > ghee_plain
    assert kerala.parameter_summary["fat_type"]["source"] == "regional_prior"


def test_precedence_override_beats_regional_prior(engine):
    r = engine.compute("PY-T-000100", n_samples=500, region_key="KL",
                       param_overrides={"fat_type": "gingelly"})
    assert r.parameter_summary["fat_type"]["source"] == "user_stated"
    assert r.parameter_summary["fat_type"]["distribution"] == {"gingelly": 1.0}


def test_supplied_prior_beats_template_default(engine):
    r = engine.compute("PY-T-000100", n_samples=N, seed=9,
                       param_priors={"fat_g": Prior("point", {"value": 25.0})})
    assert r.parameter_summary["fat_g"]["source"] == "supplied_prior"
    assert r.parameter_summary["fat_g"]["p50"] == pytest.approx(25.0)


# ---------------------------------------------------------- provenance / QC ----

def test_result_carries_sources_and_flags_uncleared_ones(engine):
    r = engine.compute("PY-T-000100", n_samples=200)
    assert {s.source_key for s in r.sources} == {"IFCT2017"}
    assert [s.source_key for s in r.uncleared_sources] == ["IFCT2017"]
    assert any("commercial clearance" in w for w in r.warnings)


def test_qc_gates_run_and_pass(engine):
    r = engine.compute("PY-T-000101", n_samples=N)
    by_gate = {q.gate: q for q in r.qc}
    assert by_gate["interval_ordering"].status == "PASS"
    assert by_gate["non_negative"].status == "PASS"
    assert by_gate["yield_plausible"].status == "PASS"
    assert by_gate["energy_atwater"].status in {"PASS", "WARN"}
    assert all(q.ok or q.status == "WARN" for q in r.qc)


def test_min_confidence_warns_on_weak_tiers(repo):
    from pathyam_engine.models import CompositionValue
    repo._composition[1].append(
        CompositionValue(food_id=1, nutrient_id=5, value=999.0, confidence="D",
                         source_key="PATHYAM-EST", is_borrowed=False)
    )
    r = ComputeEngine(repo).compute("PY-T-000100", n_samples=200, min_confidence="B")
    assert r.worst_confidence == "D"
    assert any("tier" in w for w in r.warnings)


# ------------------------------------------------------------------ errors ----

def test_unknown_template_raises(engine):
    with pytest.raises(KeyError):
        engine.compute("PY-T-999999")


def test_zero_servings_rejected(engine):
    with pytest.raises(EngineError, match="servings must be positive"):
        engine.compute("PY-T-000100", servings=0)


def test_zero_mass_template_raises(engine):
    with pytest.raises(EngineError, match="non-positive total mass"):
        engine.compute(
            "PY-T-000100", n_samples=50,
            param_overrides={"batter_g": 0.0, "rice_fraction": 0.75,
                             "fat_g": 0.0, "fat_type": "gingelly"},
        )


def test_missing_composition_warns_but_does_not_crash(repo):
    from pathyam_engine.models import RecipeTemplate, TemplateIngredient, TemplateParameter
    repo._by_key["PY-T-000900"] = RecipeTemplate(
        template_id=900, pathyam_id="PY-T-000900", food_id=999, base_method="raw",
        parameters=[TemplateParameter("qty", "continuous", "point", {"value": 50.0}, unit="g")],
        ingredients=[TemplateIngredient("qty", food_id=999)],
        yield_factor=1.0,
    )
    repo._by_id[900] = repo._by_key["PY-T-000900"]
    r = ComputeEngine(repo).compute("PY-T-000900", n_samples=50)
    assert any("no composition data" in w for w in r.warnings)
    assert r.nutrients == {}
