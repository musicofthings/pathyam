"""Template authoring: schema validation, loader, audit.

The validator is the deliverable here — ~400 templates cannot be review-gated, so
every check below corresponds to a mistake that would otherwise ship a template that
computes happily and returns a wrong number.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pathyam_engine.authoring import audit as run_audit
from pathyam_engine.authoring import load_library

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "db" / "templates"


@pytest.fixture(scope="module")
def library():
    return load_library(
        TEMPLATE_DIR / "ingredients.yaml",
        sorted(p for p in TEMPLATE_DIR.glob("*.yaml") if p.name != "ingredients.yaml"),
    )


def _write(tmp_path, ingredients: str, templates: str):
    (tmp_path / "ingredients.yaml").write_text(ingredients, encoding="utf-8")
    (tmp_path / "t.yaml").write_text(templates, encoding="utf-8")
    return load_library(tmp_path / "ingredients.yaml", [tmp_path / "t.yaml"])


_ING = """
ingredients:
  - {key: rice, en: "Rice, raw", group: cereal}
  - {key: oil_a, en: "Oil A", group: fat}
  - {key: oil_b, en: "Oil B", group: fat}
"""


# ------------------------------------------------------- the shipped library ----

def test_shipped_library_validates_clean(library):
    assert library.ok, "\n".join(str(i) for i in library.errors)
    assert not library.warnings, "\n".join(str(i) for i in library.warnings)


def test_shipped_library_is_substantial(library):
    assert len(library.templates) >= 15
    assert len(library.ingredients) >= 45


def test_every_template_covers_the_major_cooking_methods(library):
    methods = {t.method for t in library.templates.values()}
    assert {"steamed", "griddled", "deep_fried", "simmered", "assembled"} <= methods


def test_library_exercises_nested_sub_recipes(library):
    nested = [t for t in library.templates.values()
              if any(r.sub_template for r in t.ingredients)]
    assert len(nested) >= 2, "sub-recipe nesting should be represented"


def test_deep_fried_models_oil_as_a_fraction_not_a_fixed_mass(library):
    """Absorption scales with batter mass; a fixed-gram model collapses the variance."""
    vada = library.templates["PY-T-000114"]
    assert "oil_absorbed_frac" in vada.parameters
    oil_rows = [r for r in vada.ingredients if r.food and "oil" in r.food]
    assert oil_rows and all("oil_absorbed_frac" in r.qty for r in oil_rows)


def test_fermented_batter_templates_are_on_a_dry_solids_basis(library):
    """Regression: wet-batter mass valued against dry-weight IFCT overstated ~2.6x."""
    for tid in ("PY-T-000110", "PY-T-000111", "PY-T-000113"):
        tpl = library.templates[tid]
        assert "solids_g" in tpl.parameters, f"{tid} should use dry solids"
        assert "batter_g" not in tpl.parameters
        assert tpl.yield_factor and tpl.yield_factor > 1.0, (
            f"{tid} yield {tpl.yield_factor} — dry solids gain mass when cooked"
        )


# ------------------------------------------------------------ error catching ----

def test_unknown_ingredient_key_is_an_error(tmp_path):
    lib = _write(tmp_path, _ING, """
templates:
  - id: PY-T-000001
    dish: x
    method: steamed
    servings: 1
    parameters: {a: {dist: point, value: 10, unit: g}}
    ingredients:
      - {food: nonexistent, qty: 'a'}
""")
    assert not lib.ok
    assert any("unknown ingredient key" in str(i) for i in lib.errors)


def test_expression_referencing_undeclared_parameter_is_an_error(tmp_path):
    lib = _write(tmp_path, _ING, """
templates:
  - id: PY-T-000001
    dish: x
    method: steamed
    servings: 1
    parameters: {a: {dist: point, value: 10, unit: g}}
    ingredients:
      - {food: rice, qty: 'a * typo_param'}
""")
    assert any("undeclared parameter" in str(i) for i in lib.errors)


def test_unsafe_expression_is_rejected(tmp_path):
    lib = _write(tmp_path, _ING, """
templates:
  - id: PY-T-000001
    dish: x
    method: steamed
    servings: 1
    parameters: {a: {dist: point, value: 10, unit: g}}
    ingredients:
      - {food: rice, qty: '__import__("os").system("id")'}
""")
    assert not lib.ok


def test_malformed_prior_is_caught_at_authoring_time(tmp_path):
    """A prior that only fails at request time is a production incident."""
    lib = _write(tmp_path, _ING, """
templates:
  - id: PY-T-000001
    dish: x
    method: steamed
    servings: 1
    parameters: {a: {dist: lognormal, mean: 10, unit: g}}
    ingredients:
      - {food: rice, qty: 'a'}
""")
    assert any("missing required key" in str(i) for i in lib.errors)


def test_implausible_yield_factor_is_an_error(tmp_path):
    lib = _write(tmp_path, _ING, """
templates:
  - id: PY-T-000001
    dish: x
    method: steamed
    servings: 1
    yield_factor: 40
    parameters: {a: {dist: point, value: 10, unit: g}}
    ingredients:
      - {food: rice, qty: 'a'}
""")
    assert any("implausible" in str(i) for i in lib.errors)


def test_uncovered_categorical_category_is_an_error(tmp_path):
    """The silent one: a category with prior weight that no ingredient selects.

    That share of Monte Carlo draws contains none of the component — usually cooking
    fat — and nothing raises. The energy estimate is just quietly low.
    """
    lib = _write(tmp_path, _ING, """
templates:
  - id: PY-T-000001
    dish: x
    method: griddled
    servings: 1
    parameters:
      a: {dist: point, value: 10, unit: g}
      fat_type: {dist: categorical, categories: [a_oil, b_oil], weights: [0.7, 0.3]}
    ingredients:
      - {food: rice, qty: 'a'}
      - {food: oil_a, qty: 'a * (fat_type == "a_oil")'}
""")
    errors = [str(i) for i in lib.errors]
    assert any("b_oil" in e and "30%" in e for e in errors), errors


def test_selector_on_a_category_that_does_not_exist_is_an_error(tmp_path):
    lib = _write(tmp_path, _ING, """
templates:
  - id: PY-T-000001
    dish: x
    method: griddled
    servings: 1
    parameters:
      a: {dist: point, value: 10, unit: g}
      fat_type: {dist: categorical, categories: [a_oil], weights: [1.0]}
    ingredients:
      - {food: oil_a, qty: 'a * (fat_type == "a_oil")'}
      - {food: oil_b, qty: 'a * (fat_type == "ghost")'}
""")
    assert any("not categories of this parameter" in str(i) for i in lib.errors)


def test_regional_override_of_undeclared_parameter_is_an_error(tmp_path):
    lib = _write(tmp_path, _ING, """
templates:
  - id: PY-T-000001
    dish: x
    method: steamed
    servings: 1
    parameters: {a: {dist: point, value: 10, unit: g}}
    ingredients:
      - {food: rice, qty: 'a'}
    regional:
      KL: {b: {dist: point, value: 5}}
""")
    assert any("does not declare" in str(i) for i in lib.errors)


def test_regional_categorical_must_keep_the_same_category_set(tmp_path):
    lib = _write(tmp_path, _ING, """
templates:
  - id: PY-T-000001
    dish: x
    method: griddled
    servings: 1
    parameters:
      a: {dist: point, value: 10, unit: g}
      fat_type: {dist: categorical, categories: [a_oil, b_oil], weights: [0.5, 0.5]}
    ingredients:
      - {food: oil_a, qty: 'a * (fat_type == "a_oil")'}
      - {food: oil_b, qty: 'a * (fat_type == "b_oil")'}
    regional:
      KL: {fat_type: {dist: categorical, categories: [a_oil, c_oil], weights: [0.5, 0.5]}}
""")
    assert any("category set differs" in str(i) for i in lib.errors)


def test_yaml_syntax_error_reports_location_not_a_traceback(tmp_path):
    """Authors hand-edit these; an unquoted colon must not surface as a Python crash."""
    lib = _write(tmp_path, _ING, """
templates:
  - id: PY-T-000001
    notes: Depth-2 nesting: this colon breaks YAML
""")
    assert not lib.ok
    assert any("YAML parse error" in str(i) and "line" in str(i) for i in lib.errors)


def test_missing_ingredient_or_sub_template_reference(tmp_path):
    lib = _write(tmp_path, _ING, """
templates:
  - id: PY-T-000001
    dish: x
    method: steamed
    servings: 1
    parameters: {a: {dist: point, value: 10, unit: g}}
    ingredients:
      - {qty: 'a'}
""")
    assert any("exactly one of food or sub_template" in str(i) for i in lib.errors)


# ---------------------------------------------------------------- integration ----

DSN = os.environ.get("PATHYAM_TEST_DSN")


@pytest.mark.skipif(not DSN, reason="PATHYAM_TEST_DSN not set")
def test_load_and_audit_against_postgres(library):
    import psycopg

    from pathyam_engine.authoring import load_into_postgres
    from pathyam_engine.repository import PostgresRepository

    with psycopg.connect(DSN) as conn:
        result = load_into_postgres(library, conn)
        assert result.templates_written == len(library.templates)
        assert result.ingredients_written > 100

        # Idempotent: a second load must not duplicate children.
        second = load_into_postgres(library, conn)
        assert second.templates_written == result.templates_written
        assert second.foods_created == 0

        report = run_audit(library, PostgresRepository(conn), n_samples=300)

    assert report.statuses
    # The audit's job is the worklist, and it must name specific ingredients.
    assert report.gaps, "expected an ingredient worklist"
    assert all(g.blocked_count > 0 for g in report.gaps)
    assert report.gaps[0].blocked_count >= report.gaps[-1].blocked_count


@pytest.mark.skipif(not DSN, reason="PATHYAM_TEST_DSN not set")
def test_computable_templates_land_in_a_plausible_energy_band(library):
    import psycopg

    from pathyam_engine.repository import PostgresRepository

    with psycopg.connect(DSN) as conn:
        report = run_audit(library, PostgresRepository(conn), n_samples=400)

    for status in report.computable:
        assert not status.qc_failures, (
            f"{status.template_id} ({status.dish}) failed QC: {status.qc_failures} "
            f"at {status.energy_per_100g:.0f} kcal/100g"
        )
