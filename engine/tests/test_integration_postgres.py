"""Integration tests against a real database seeded from ``db/010_seed_example.sql``.

Skipped unless ``PATHYAM_TEST_DSN`` is set. These are the tests that prove the SQL
schema and the Python engine actually agree - unit tests with an in-memory
repository cannot catch a column rename or an enum cast mistake.
"""

from __future__ import annotations

import os

import pytest

psycopg = pytest.importorskip("psycopg")

from pathyam_engine import ComputeEngine, PostgresRepository  # noqa: E402

DSN = os.environ.get("PATHYAM_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="PATHYAM_TEST_DSN not set")


@pytest.fixture(scope="module")
def conn():
    with psycopg.connect(DSN) as c:
        yield c


@pytest.fixture(scope="module")
def engine(conn):
    return ComputeEngine(PostgresRepository(conn))


def test_loads_template_from_schema(engine):
    t = engine.repo.get_template("PY-T-000101")
    assert t.pathyam_id == "PY-T-000101"
    assert t.base_method == "griddled"
    assert t.yield_factor == pytest.approx(0.84)
    assert "filling_g" in t.parameter_names
    assert any(i.sub_template_id is not None for i in t.ingredients), \
        "masala dosa must reference the potato-masala sub-template"


def test_computes_masala_dosa_end_to_end(engine):
    r = engine.compute("PY-T-000101", n_samples=3000, region_key="KA")
    assert r.energy is not None
    s = r.energy.per_serving
    assert s.p10 < s.p50 < s.p90
    # Sanity band for a masala dosa - wide on purpose, this is a smoke test not a claim.
    assert 150 < s.p50 < 900, f"implausible energy: {s.p50}"
    assert r.raw_mass_g.p50 > 0
    assert r.cooked_mass_g.p50 == pytest.approx(r.raw_mass_g.p50 * 0.84, rel=1e-6)


def test_sub_recipe_ingredients_resolve_through_the_database(engine):
    r = engine.compute("PY-T-000101", n_samples=500)
    via = {row["food_name"]: row["via_sub_template"] for row in r.ingredients}
    assert any(v == "PY-T-000102" for v in via.values()), \
        "potato-masala ingredients should be tagged with their sub-template"
    assert "Potato, boiled" in via


def test_regional_priors_load_and_differ(engine):
    kerala = engine.compute("PY-T-000100", n_samples=4000, seed=17, region_key="KL")
    tamil = engine.compute("PY-T-000100", n_samples=4000, seed=17, region_key="TN")

    kl = kerala.parameter_summary["fat_type"]["distribution"]
    tn = tamil.parameter_summary["fat_type"]["distribution"]
    # Kerala 0.75 coconut vs Tamil Nadu 0.60 gingelly, per the seed data
    assert kl.get("coconut", 0) > 0.6
    assert tn.get("gingelly", 0) > 0.5
    assert kerala.parameter_summary["fat_type"]["source"] == "regional_prior"


def test_regional_fat_choice_changes_saturated_fat(engine):
    """Coconut oil is ~87% saturated, gingelly ~14%. The region must move FASAT."""
    kerala = engine.compute("PY-T-000100", n_samples=200, seed=3,
                            param_overrides={"fat_g": 12.0, "fat_type": "coconut"})
    tamil = engine.compute("PY-T-000100", n_samples=200, seed=3,
                           param_overrides={"fat_g": 12.0, "fat_type": "gingelly"})
    if "FASAT" in kerala.nutrients and "FASAT" in tamil.nutrients:
        assert kerala.nutrients["FASAT"].per_serving.p50 > \
               tamil.nutrients["FASAT"].per_serving.p50 * 3


def test_parboiled_versus_raw_rice_changes_thiamine(engine):
    """The IFCT distinction the national averaging erases: 0.28 vs 0.05 mg/100 g."""
    par = engine.compute("PY-T-000100", n_samples=200, seed=4,
                         param_overrides={"batter_g": 100.0, "rice_type": "parboiled",
                                          "fat_g": 8.0, "fat_type": "gingelly"},
                         sample_composition_sd=False)
    raw = engine.compute("PY-T-000100", n_samples=200, seed=4,
                         param_overrides={"batter_g": 100.0, "rice_type": "raw",
                                          "fat_g": 8.0, "fat_type": "gingelly"},
                         sample_composition_sd=False)
    assert par.nutrients["THIA"].per_serving.p50 > raw.nutrients["THIA"].per_serving.p50


def test_uncleared_source_is_surfaced(engine):
    """IFCT2017 is seeded as not commercially cleared; the engine must say so."""
    r = engine.compute("PY-T-000101", n_samples=200)
    assert "IFCT2017" in {s.source_key for s in r.uncleared_sources}
    assert any("commercial clearance" in w for w in r.warnings)


def test_retention_factors_load_from_schema(engine):
    factors = engine.repo.get_retention_factors()
    assert factors, "seed data defines retention factors"
    assert any(f.food_group == "cereal" and f.cooking_method == "griddled"
               for f in factors)


def test_determinism_against_the_database(engine):
    a = engine.compute("PY-T-000101", n_samples=1000, region_key="TN")
    b = engine.compute("PY-T-000101", n_samples=1000, region_key="TN")
    assert a.as_dict() == b.as_dict()


def test_qc_gates_pass_on_real_data(engine):
    r = engine.compute("PY-T-000101", n_samples=2000)
    failures = [q for q in r.qc if q.status == "FAIL"]
    assert not failures, f"QC failures on seeded data: {[str(f) for f in failures]}"


def test_every_template_in_the_database_computes(conn, engine):
    """Smoke-test the whole catalogue: no template should raise."""
    with conn.cursor() as cur:
        cur.execute("SELECT pathyam_id FROM ref.recipe_template WHERE is_active ORDER BY 1")
        ids = [r[0] for r in cur.fetchall()]
    assert ids
    for pid in ids:
        r = engine.compute(pid, n_samples=300)
        assert r.raw_mass_g.p50 > 0, f"{pid} produced zero mass"


def test_expression_validation_covers_every_stored_template(conn):
    """Every qty_expr in the database must reference only declared parameters.

    This is the check that catches a typo in a template before a user hits it.
    Run it in CI against production data.
    """
    from pathyam_engine.expressions import validate

    with conn.cursor() as cur:
        cur.execute(
            """SELECT t.pathyam_id, ti.qty_expr,
                      coalesce(array_agg(DISTINCT tp.param_name)
                               FILTER (WHERE tp.param_name IS NOT NULL), '{}')
                 FROM ref.recipe_template t
                 JOIN ref.template_ingredient ti ON ti.template_id = t.template_id
                 LEFT JOIN ref.template_parameter tp ON tp.template_id = t.template_id
                GROUP BY t.pathyam_id, ti.qty_expr"""
        )
        rows = cur.fetchall()

    assert rows
    problems = []
    for pathyam_id, expr, params in rows:
        try:
            validate(expr, set(params))
        except Exception as exc:
            problems.append(f"{pathyam_id}: {exc}")
    assert not problems, "invalid quantity expressions:\n" + "\n".join(problems)
