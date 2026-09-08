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


# ------------------------------------------- composition loader idempotency ----

def test_reloading_composition_on_a_later_day_does_not_double_a_nutrient(conn):
    """Regression: a loader re-run on a new date used to insert a second live row.

    composition_unique_ck is UNIQUE (food_id, nutrient_id, basis, valid_from) and
    valid_from defaults to current_date, so ON CONFLICT only matched rows written the
    same day. The engine sums every row with valid_to IS NULL, so the nutrient
    silently doubled: puttu went 272 -> 471 kcal/100 g overnight with no code change.
    Loaders now close the previous row instead of inserting beside it.
    """
    from pathyam_engine.authoring.derived_foods import _supersede_open_rows

    with conn.cursor() as cur:
        cur.execute("SELECT food_id FROM ref.food_item LIMIT 1")
        food_id = cur.fetchone()[0]
        cur.execute("SELECT nutrient_id FROM ref.nutrient LIMIT 1")
        nutrient_id = cur.fetchone()[0]

        # A row as it would have been written on an earlier day.
        cur.execute(
            """INSERT INTO ref.composition_value
                   (food_id, nutrient_id, value, basis, confidence, source_id,
                    valid_from)
               SELECT %s, %s, 100, 'per_100g', 'B', min(source_id),
                      current_date - 1
                 FROM ref.source
               ON CONFLICT DO NOTHING""",
            (food_id, nutrient_id),
        )

        _supersede_open_rows(cur, food_id, nutrient_id)

        # Today's write lands alongside, but only one row is live.
        cur.execute(
            """INSERT INTO ref.composition_value
                   (food_id, nutrient_id, value, basis, confidence, source_id)
               SELECT %s, %s, 200, 'per_100g', 'B', min(source_id) FROM ref.source
               ON CONFLICT (food_id, nutrient_id, basis, valid_from)
               DO UPDATE SET value = EXCLUDED.value""",
            (food_id, nutrient_id),
        )

        cur.execute(
            """SELECT count(*), max(value) FROM ref.composition_value
                WHERE food_id = %s AND nutrient_id = %s AND basis = 'per_100g'
                  AND valid_to IS NULL""",
            (food_id, nutrient_id),
        )
        live, value = cur.fetchone()

    conn.rollback()
    assert live == 1, "exactly one composition row may be live per food/nutrient/basis"
    assert float(value) == 200.0, "the live row must be the most recent write"


def test_no_food_has_two_live_values_for_the_same_nutrient(conn):
    """Guards the whole table, not just the path the test above exercises."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT count(*) FROM (
                   SELECT food_id, nutrient_id, basis
                     FROM ref.composition_value
                    WHERE valid_to IS NULL
                    GROUP BY 1, 2, 3 HAVING count(*) > 1
               ) duplicated"""
        )
        assert cur.fetchone()[0] == 0


# ------------------------------------------------------- evidence corpus ----

def _seed_evidence(cur):
    cur.execute("DELETE FROM ref.evidence_document")
    cur.executemany(
        """INSERT INTO ref.evidence_document
               (tier, pmid, title, abstract, journal, authors, publication_year,
                retrieved_via)
           VALUES ('TIER_2_PUBMED', %s, %s, %s, %s, %s, %s, 'test')""",
        [
            ("35875218",
             "Glycemic carbohydrates, glycemic index, and glycemic load of South Indian foods",
             "Idli showed a lower glycemic index than white rice, attributed to urad dal "
             "and natural fermentation of the batter.",
             "J Food Sci Technol", ["Shakappa D"], 2022),
            ("24587528",
             "Evaluation of finger millet incorporated noodles",
             "Finger millet noodles produced a lower postprandial glucose response than "
             "refined wheat noodles in healthy adults.",
             "J Food Sci Technol", ["Shukla K"], 2014),
        ],
    )


def test_evidence_retrieval_ranks_by_full_text_relevance(conn):
    from pathyam_engine.evidence.hybrid_retrieval import PostgresEvidenceRetriever

    with conn.cursor() as cur:
        _seed_evidence(cur)
        hits = PostgresEvidenceRetriever(conn).retrieve("idli glycemic index", limit=5)
        assert hits, "a well-matched query must retrieve something"
        assert hits[0].pmid == "35875218"
        assert hits[0].score > 0
        assert hits == sorted(hits, key=lambda d: -d.score)
    conn.rollback()


def test_a_natural_language_question_still_retrieves(conn):
    """websearch_to_tsquery ANDs its terms, so questions returned nothing at all."""
    from pathyam_engine.evidence.hybrid_retrieval import PostgresEvidenceRetriever

    with conn.cursor() as cur:
        _seed_evidence(cur)
        hits = PostgresEvidenceRetriever(conn).retrieve(
            "does idli have a lower glycemic index than white rice"
        )
        assert hits and hits[0].pmid == "35875218"
    conn.rollback()


def test_an_unrelated_query_retrieves_nothing(conn):
    """OR'd terms plus stemming made 'topic' match 'topical'; a rank floor stops it."""
    from pathyam_engine.evidence.hybrid_retrieval import PostgresEvidenceRetriever

    with conn.cursor() as cur:
        _seed_evidence(cur)
        assert PostgresEvidenceRetriever(conn).retrieve("purple aeroplane sonata") == []
    conn.rollback()


def test_every_corpus_document_carries_an_identifier(conn):
    """A document nobody can verify has no business in a clinical evidence corpus."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT count(*) FROM ref.evidence_document
                WHERE pmid IS NULL AND doi IS NULL AND guideline_ref IS NULL"""
        )
        assert cur.fetchone()[0] == 0


# ------------------------------------------- research cohorts + release gate ----
#
# db/018. The point of these is the compliance boundary, not the ingest: a
# NonCommercial research dataset must be visible to the release gate and must never
# be mixed with app.* user data.


def _load_cgmacros_fixture(conn, tmp_path):
    """Two participants' worth of synthetic rows, real column names."""
    from pathyam_engine.authoring.cgmacros import load_cgmacros_into_postgres

    header = ("Timestamp,Libre GL,Dexcom GL,HR,Calories (Activity),Mets,Meal Type,"
              "Calories,Carbs,Protein,Fat,Fiber,Amount Consumed,Image Path")
    for n in (1, 2):
        (tmp_path / f"CGMacros-00{n}.csv").write_text(
            header + "\n"
            "01/02/2021 12:30,140,145,70,2.0,12,Lunch,620,75,28,22,9,100,p.jpg\n"
            "01/02/2021 12:45,165,170,72,2.0,12,,,,,,,,\n"
            "01/02/2021 13:30,150,152,70,2.0,12,,,,,,,,\n"
            "01/02/2021 18:00,110,112,68,1.5,11,,,,,,,,\n",
            encoding="utf-8",
        )
    (tmp_path / "bio.csv").write_text(
        "Age,Gender,BMI,Self-identify ,A1c PDL (Lab),Fasting GLU - PDL (Lab)\n"
        "34,F,27.4,Hispanic/Latino,5.9,104\n"
        "58,M,31.2,White,7.1,141\n",
        encoding="utf-8",
    )
    return load_cgmacros_into_postgres(tmp_path, conn, dry_run=False)


def test_a_noncommercial_research_cohort_reaches_the_release_gate(conn, tmp_path):
    """The gap this schema was written to close.

    ref.v_uncleared_values only ever joined ref.composition_value, so a research
    dataset registered with is_commercial_cleared = false was invisible to it: the
    flag was set correctly and checked nowhere. ref.v_release_blockers unions both
    asset classes, and this asserts CGMacros actually appears there.
    """
    result = _load_cgmacros_fixture(conn, tmp_path)
    assert result.subjects == 2
    # 2 participants x 4 timestamps x 2 sensors.
    assert result.readings == 16
    assert result.meals == 2

    with conn.cursor() as cur:
        cur.execute(
            "SELECT source_key, licence, row_count, detail "
            "  FROM ref.v_release_blockers WHERE asset = 'research_cohort'")
        rows = cur.fetchall()

    assert rows, "the cohort did not reach the release gate"
    source_key, licence, row_count, detail = rows[0]
    assert source_key == "CGMACROS-1.0.0"
    assert "NonCommercial" in licence
    assert row_count > 0
    assert "none South Asian" in detail, \
        "the gate must show why the cohort does not transfer, not just that it is uncleared"


def test_the_cohort_is_never_written_into_app_user_data(conn, tmp_path):
    """Research participants consented to a different study, years ago.

    Loading them as app.app_user rows would manufacture users who agreed to nothing
    here, and would either bypass the consent check added in db/017 or require
    forging consent rows.
    """
    before = _count(conn, "SELECT count(*) FROM app.app_user")
    _load_cgmacros_fixture(conn, tmp_path)

    assert _count(conn, "SELECT count(*) FROM app.app_user") == before
    assert _count(conn, "SELECT count(*) FROM app.cgm_reading") == 0
    assert _count(conn, "SELECT count(*) FROM research.cgm_reading") > 0


def test_the_dataset_row_declares_that_portions_were_not_weighed(conn, tmp_path):
    """Stops CGMacros being mistaken for the golden meal set.

    Its portion field is a percentage estimated from photographs. The golden meal
    set exists because portion accuracy needs weighed component masses.
    """
    _load_cgmacros_fixture(conn, tmp_path)
    with conn.cursor() as cur:
        cur.execute("""SELECT portions_are_weighed, timestamps_are_shifted
                         FROM research.dataset WHERE dataset_key = 'CGMACROS-1.0.0'""")
        weighed, shifted = cur.fetchone()
    assert weighed is False
    assert shifted is True


def test_postprandial_pairing_works_on_the_research_cohort(conn, tmp_path):
    """The reason to load this at all: app.v_postprandial_reading had never run
    against real traces. The research view is the same shape and same rules."""
    _load_cgmacros_fixture(conn, tmp_path)
    with conn.cursor() as cur:
        cur.execute("""SELECT sensor, minutes_since_meal, glucose_mg_dl
                         FROM research.v_postprandial_reading
                        WHERE dataset_key = 'CGMACROS-1.0.0'
                        ORDER BY sensor, minutes_since_meal""")
        rows = cur.fetchall()

    assert rows, "no readings paired to a meal"
    # The 18:00 reading is 5.5 h after the 12:30 lunch and must fall outside the 3 h window.
    assert all(0 <= minutes < 180 for _s, minutes, _g in rows)
    assert {r[0] for r in rows} == {"Dexcom G6 Pro", "FreeStyle Libre Pro"}


def test_a_re_run_updates_rather_than_duplicating(conn, tmp_path):
    """A doubled reading silently biases any curve fitted from the table — the
    failure the IFCT loader hit across a date boundary."""
    _load_cgmacros_fixture(conn, tmp_path)
    after_first = _count(conn, "SELECT count(*) FROM research.cgm_reading")
    _load_cgmacros_fixture(conn, tmp_path)
    assert _count(conn, "SELECT count(*) FROM research.cgm_reading") == after_first


def _count(conn, sql: str) -> int:
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchone()[0]
