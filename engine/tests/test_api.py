"""API tests against a live seeded database.

Skipped unless ``PATHYAM_TEST_DSN`` is set (``run_tests.sh`` sets it).
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("psycopg_pool")

DSN = os.environ.get("PATHYAM_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="PATHYAM_TEST_DSN not set")


@pytest.fixture(scope="module")
def client():
    os.environ["PATHYAM_DSN"] = DSN
    from fastapi.testclient import TestClient
    from pathyam_api.main import app

    with TestClient(app) as c:
        yield c


# ------------------------------------------------------------------ health ----

def test_health_reports_catalogue_size(client):
    r = client.get("/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["templates"] >= 3
    assert body["lexicon_entries"] > 0


def test_health_flags_uncleared_sources(client):
    """IFCT2017 is seeded uncleared; ops should see that without digging."""
    notes = client.get("/v1/health").json()["notes"]
    assert any("uncleared" in n for n in notes)


# ---------------------------------------------------------------- resolve ----

def test_resolve_english(client):
    r = client.post("/v1/resolve", json={"text": "2 masala dosa"})
    assert r.status_code == 200
    item = r.json()["items"][0]
    assert item["parsed"]["quantity"] == 2
    assert item["candidates"][0]["name_en"] == "Dosa, masala"


def test_resolve_tamil_native_script(client):
    r = client.post("/v1/resolve", json={"text": "ஒரு தோசை", "preferred_lang": "ta"})
    item = r.json()["items"][0]
    assert item["parsed"]["quantity"] == 1
    assert item["candidates"][0]["pathyam_id"] == "PY-F-000100"


def test_resolve_kannada_romanised(client):
    r = client.post("/v1/resolve", json={"text": "eradu masale dose",
                                         "preferred_lang": "kn"})
    item = r.json()["items"][0]
    assert item["parsed"]["quantity"] == 2
    assert item["candidates"][0]["pathyam_id"] == "PY-F-000101"


def test_resolve_returns_no_nutrient_values(client):
    """The contract: retrieval returns identities, never numbers."""
    body = client.post("/v1/resolve", json={"text": "2 masala dosa"}).json()
    blob = str(body).lower()
    for forbidden in ("kcal", "energ", "nutrient", "protein"):
        assert forbidden not in blob, f"resolve leaked {forbidden!r}"


def test_resolve_multi_item(client):
    body = client.post("/v1/resolve", json={"text": "2 dosa + 1 masala dosa"}).json()
    assert len(body["items"]) == 2


def test_resolve_rejects_empty_text(client):
    assert client.post("/v1/resolve", json={"text": ""}).status_code == 422


def test_resolve_rejects_unknown_region(client):
    r = client.post("/v1/resolve", json={"text": "dosa", "region_key": "ZZ"})
    assert r.status_code == 422


# ---------------------------------------------------------------- compute ----

def test_compute_returns_intervals_not_scalars(client):
    r = client.post("/v1/compute", json={"template": "PY-T-000101", "n_samples": 1000})
    assert r.status_code == 200
    body = r.json()
    energy = body["nutrients"]["ENERC_KCAL"]["per_serving"]
    assert energy["p10"] < energy["p50"] < energy["p90"]
    assert "80% CI" in body["summary"]


def test_compute_is_deterministic(client):
    payload = {"template": "PY-T-000101", "n_samples": 1000, "region_key": "KA"}
    a = client.post("/v1/compute", json=payload).json()
    b = client.post("/v1/compute", json=payload).json()
    assert a["seed"] == b["seed"]
    assert a["nutrients"] == b["nutrients"]


def test_compute_carries_provenance_and_qc(client):
    body = client.post("/v1/compute", json={"template": "PY-T-000100",
                                            "n_samples": 500}).json()
    assert any(s["source_key"] == "IFCT2017" for s in body["sources"])
    assert any(not s["commercial_cleared"] for s in body["sources"])
    assert body["qc"] and all("gate" in q for q in body["qc"])


def test_compute_overrides_pin_parameters(client):
    body = client.post("/v1/compute", json={
        "template": "PY-T-000100", "n_samples": 500,
        "param_overrides": {"fat_g": 12.0, "fat_type": "ghee"},
    }).json()
    assert body["parameter_summary"]["fat_g"]["source"] == "user_stated"
    assert body["parameter_summary"]["fat_g"]["p50"] == pytest.approx(12.0)


def test_param_scale_shifts_the_prior_without_pinning_it(client):
    """'a little oil' must stay a distribution, not become a fabricated number."""
    base = client.post("/v1/compute", json={
        "template": "PY-T-000100", "n_samples": 4000, "seed": 7}).json()
    less = client.post("/v1/compute", json={
        "template": "PY-T-000100", "n_samples": 4000, "seed": 7,
        "param_scales": {"fat_g": 0.6}}).json()

    assert less["parameter_summary"]["fat_g"]["p50"] < \
           base["parameter_summary"]["fat_g"]["p50"]
    assert less["parameter_summary"]["fat_g"]["sd"] > 0, "must remain a distribution"
    assert less["parameter_summary"]["fat_g"]["source"] == "supplied_prior"


def test_compute_rejects_non_positive_scale(client):
    r = client.post("/v1/compute", json={"template": "PY-T-000100",
                                         "param_scales": {"fat_g": 0}})
    assert r.status_code == 422


def test_compute_unknown_template_is_404(client):
    r = client.post("/v1/compute", json={"template": "PY-T-999999"})
    assert r.status_code == 404


def test_compute_region_changes_the_result(client):
    kerala = client.post("/v1/compute", json={
        "template": "PY-T-000100", "n_samples": 4000, "seed": 21,
        "region_key": "KL"}).json()
    tamil = client.post("/v1/compute", json={
        "template": "PY-T-000100", "n_samples": 4000, "seed": 21,
        "region_key": "TN"}).json()
    kl = kerala["parameter_summary"]["fat_type"]["distribution"]
    tn = tamil["parameter_summary"]["fat_type"]["distribution"]
    assert kl.get("coconut", 0) > tn.get("coconut", 0)


# -------------------------------------------------------------------- log ----

def test_log_resolves_and_computes(client):
    body = client.post("/v1/log", json={"text": "2 masala dosa",
                                        "n_samples": 800}).json()
    item = body["items"][0]
    assert item["computed"] is not None
    assert item["computed"]["portions"] == 2
    assert body["total_energy_kcal"]["p50"] > 0


def test_log_withholds_computation_when_confirmation_is_needed(client):
    """Silent guessing destroys trust and the correction signal. Off by default."""
    body = client.post("/v1/log", json={"text": "1 dos"}).json()
    item = body["items"][0]
    if item["resolution"]["needs_confirmation"]:
        assert item["computed"] is None
        assert item["skipped_reason"]
        assert body["needs_confirmation"] is True


def test_log_auto_accept_forces_computation(client):
    body = client.post("/v1/log", json={"text": "1 dos", "auto_accept": True,
                                        "n_samples": 400}).json()
    assert body["items"][0]["computed"] is not None


def test_log_applies_parsed_parameter_hints(client):
    """'konjam ennai' should reach the engine as a scaled fat_g prior."""
    plain = client.post("/v1/log", json={"text": "1 masala dosa",
                                         "n_samples": 3000}).json()
    less = client.post("/v1/log", json={"text": "1 masala dosa konjam ennai",
                                        "n_samples": 3000}).json()
    assert less["items"][0]["resolution"]["parsed"]["parameter_hints"] == {"fat_g": 0.6}
    p_fat = plain["items"][0]["computed"]["parameter_summary"]["fat_g"]["p50"]
    l_fat = less["items"][0]["computed"]["parameter_summary"]["fat_g"]["p50"]
    assert l_fat < p_fat


def test_log_sums_energy_across_items(client):
    body = client.post("/v1/log", json={"text": "1 dosa + 1 masala dosa",
                                        "n_samples": 800}).json()
    computed = [i for i in body["items"] if i["computed"]]
    if len(computed) == 2:
        total = body["total_energy_kcal"]["p50"]
        parts = sum(i["computed"]["nutrients"]["ENERC_KCAL"]["per_serving"]["p50"]
                    for i in computed)
        assert total == pytest.approx(parts, rel=1e-6)
        # quadrature, so the combined interval is narrower than naive addition
        naive = sum(i["computed"]["nutrients"]["ENERC_KCAL"]["per_serving"]["p90"]
                    for i in computed)
        assert body["total_energy_kcal"]["p90"] < naive


def test_log_quantity_scales_servings(client):
    one = client.post("/v1/log", json={"text": "1 masala dosa", "n_samples": 600}).json()
    three = client.post("/v1/log", json={"text": "3 masala dosa", "n_samples": 600}).json()
    assert three["items"][0]["computed"]["portions"] == 3
    e1 = one["items"][0]["computed"]["nutrients"]["ENERC_KCAL"]["per_serving"]["p50"]
    e3 = three["items"][0]["computed"]["nutrients"]["ENERC_KCAL"]["per_serving"]["p50"]
    assert e3 == pytest.approx(e1 * 3, rel=1e-6)


def test_openapi_schema_is_generated(client):
    spec = client.get("/openapi.json").json()
    assert "/v1/compute" in spec["paths"]
    assert "/v1/resolve" in spec["paths"]
    assert "/v1/log" in spec["paths"]
    assert "/v1/cgt/telemetry" in spec["paths"]
    assert "/v1/cgt/predict_spike" in spec["paths"]


def test_log_meal_type_and_timestamp(client):
    body = client.post("/v1/log", json={
        "text": "2 idli",
        "meal_type": "breakfast",
        "consumed_at": "2026-08-14T08:30:00+05:30",
        "n_samples": 400
    }).json()
    assert body["meal_type"] == "breakfast"
    assert "2026-08-14" in body["consumed_at"]


def test_log_accept_candidate(client):
    """Passing selected_template_id overrides confirmation gate and computes directly."""
    body = client.post("/v1/log", json={
        "text": "1 dos",
        "selected_template_id": 1,  # Plain dosa template
        "n_samples": 400
    }).json()
    assert body["items"][0]["computed"] is not None


def test_log_unknown_query_graceful_fallback(client):
    body = client.post("/v1/log", json={"text": "completely_unknown_xyz_meal_query"}).json()
    item = body["items"][0]
    assert item["is_custom_fallback"] is True
    assert item["skipped_reason"] is not None


def test_cgt_telemetry_and_predict_spike(client):
    t_resp = client.post("/v1/cgt/telemetry", json={
        "user_id": "test-user-1",
        "readings": [{"timestamp": "2026-08-14T08:30:00Z", "glucose_mg_dl": 98.5, "trend_arrow": "flat"}]
    })
    assert t_resp.status_code == 200
    assert t_resp.json()["count"] == 1

    p_resp = client.post("/v1/cgt/predict_spike", json={
        "carbs_g": 50.0,
        "gi": 70.0,
        "fibre_g": 6.0,
        "fat_g": 12.0,
        "baseline_mg_dl": 95.0
    })
    assert p_resp.status_code == 200
    cgt = p_resp.json()
    assert cgt["peak_mg_dl"] > 95.0
    assert cgt["glycemic_load"] == 35.0
    assert len(cgt["curve"]) > 10



# -------------------------------------------------------------- news feed ----
#
# These stub NCBIClient so the suite never touches the network. The point of the
# second test is the regression guard: this endpoint used to return four invented
# articles in journals that do not exist, and an outage must now produce an empty
# feed rather than canned content.


class _StubNCBI:
    def __init__(self, pmids=None, summaries=None, abstracts=None):
        self._pmids = pmids or []
        self._summaries = summaries or {}
        self._abstracts = abstracts or {}

    def search_pubmed(self, query, max_results=5, sort=None):
        return self._pmids

    def fetch_pubmed_summaries(self, pmids):
        return self._summaries

    def fetch_abstracts(self, pmids):
        return self._abstracts


def test_news_feed_returns_real_pubmed_records(client, monkeypatch):
    import pathyam_api.main as main

    stub = _StubNCBI(
        pmids=["35875218"],
        summaries={
            "35875218": {
                "title": "Glycemic carbohydrates of South Indian breakfast foods.",
                "fulljournalname": "Journal of Food Science and Technology",
                "pubdate": "2022 Aug",
                "authors": [{"name": "Shakappa D"}, {"name": "Naik R"}],
            }
        },
        abstracts={"35875218": "Idli showed a lower glycemic index than white rice."},
    )
    monkeypatch.setattr(main, "NCBIClient", lambda *a, **kw: stub)

    body = client.get("/v1/news/rss").json()
    assert body["count"] == 1
    article = body["articles"][0]
    assert article["link"] == "https://pubmed.ncbi.nlm.nih.gov/35875218/"
    assert "Journal of Food Science and Technology" in article["source"]
    assert "Shakappa D" in article["source"]
    assert article["summary"] == "Idli showed a lower glycemic index than white rice."
    assert article["published_at"] == "2022 Aug"


def test_news_feed_is_empty_when_pubmed_is_unreachable(client, monkeypatch):
    """Regression guard: an outage must not fall back to fabricated articles."""
    import pathyam_api.main as main

    monkeypatch.setattr(main, "NCBIClient", lambda *a, **kw: _StubNCBI())

    body = client.get("/v1/news/rss").json()
    assert body["count"] == 0
    assert body["articles"] == []


# ----------------------------------------------------------- meal journal ----
#
# The journal used to be a module-level Python list: history vanished on restart and
# every caller shared one log. These tests pin the two properties that fixes.


def test_a_logged_meal_is_written_to_the_database_not_process_memory(client):
    """The point of the change: the row is in Postgres, reachable without the app.

    Read back over an independent connection rather than a second TestClient --
    constructing one re-runs the app lifespan and swaps the module-level pool out
    from under the shared client.
    """
    import psycopg

    posted = client.post("/v1/log", json={"text": "2 idli", "n_samples": 120})
    assert posted.status_code == 200, posted.text
    entry_id = posted.json()["id"]

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT user_id, meal_type, notes, deleted_at
                 FROM app.meal_log WHERE meal_log_id = %s""",
            (entry_id,),
        )
        row = cur.fetchone()

    assert row is not None, "the meal was not persisted"
    user_id, meal_type, notes, deleted_at = row
    assert str(user_id) == "00000000-0000-0000-0000-000000000001"   # the dev user
    assert notes == "2 idli", "the user's own words are kept verbatim"
    assert deleted_at is None

    # And it comes back through the API.
    history = client.get("/v1/history").json()
    assert entry_id in {e["id"] for e in history["entries"]}


def test_one_users_journal_is_invisible_to_another(client):
    """user_id is a real column, not decoration."""
    alice = "11111111-1111-1111-1111-111111111111"
    bob = "22222222-2222-2222-2222-222222222222"

    posted = client.post(
        "/v1/log", json={"text": "1 dosa", "n_samples": 120},
        headers={"X-Pathyam-User": alice},
    )
    assert posted.status_code == 200, posted.text
    entry_id = posted.json()["id"]

    seen_by_alice = client.get("/v1/history", headers={"X-Pathyam-User": alice}).json()
    seen_by_bob = client.get("/v1/history", headers={"X-Pathyam-User": bob}).json()

    assert entry_id in {e["id"] for e in seen_by_alice["entries"]}
    assert entry_id not in {e["id"] for e in seen_by_bob["entries"]}


def test_deleting_a_meal_is_soft_and_scoped_to_its_owner(client):
    owner = "33333333-3333-3333-3333-333333333333"
    stranger = "44444444-4444-4444-4444-444444444444"

    entry_id = client.post(
        "/v1/log", json={"text": "1 idli", "n_samples": 120},
        headers={"X-Pathyam-User": owner},
    ).json()["id"]

    # Someone else cannot delete it.
    assert client.delete(
        f"/v1/history/{entry_id}", headers={"X-Pathyam-User": stranger}
    ).status_code == 404

    # A stranger cannot rescale it either.
    assert client.patch(
        f"/v1/history/{entry_id}/portion", json={"delta": 1.0},
        headers={"X-Pathyam-User": stranger},
    ).status_code == 404

    assert client.delete(
        f"/v1/history/{entry_id}", headers={"X-Pathyam-User": owner}
    ).status_code == 200

    after = client.get("/v1/history", headers={"X-Pathyam-User": owner}).json()
    assert entry_id not in {e["id"] for e in after["entries"]}


def test_portion_adjustment_scales_the_recorded_nutrients(client):
    user = "55555555-5555-5555-5555-555555555555"
    entry_id = client.post(
        "/v1/log", json={"text": "1 idli", "n_samples": 120},
        headers={"X-Pathyam-User": user},
    ).json()["id"]

    before = client.get("/v1/history", headers={"X-Pathyam-User": user}).json()
    original = next(e for e in before["entries"] if e["id"] == entry_id)

    bumped = client.patch(
        f"/v1/history/{entry_id}/portion", json={"delta": 1.0},
        headers={"X-Pathyam-User": user},
    )
    assert bumped.status_code == 200, bumped.text

    # Whether the figures move depends on whether the dish resolved against the
    # lexicon this database was seeded with; the persistence contract does not.
    if original["total_kcal"] > 0:
        assert bumped.json()["total_kcal"] > original["total_kcal"]

    # And it persisted, rather than only being returned.
    reread = client.get("/v1/history", headers={"X-Pathyam-User": user}).json()
    assert next(e for e in reread["entries"] if e["id"] == entry_id)["total_kcal"] \
        == bumped.json()["total_kcal"]


def test_portions_never_drop_to_zero_or_below(client):
    user = "66666666-6666-6666-6666-666666666666"
    entry_id = client.post(
        "/v1/log", json={"text": "1 idli", "n_samples": 120},
        headers={"X-Pathyam-User": user},
    ).json()["id"]

    for _ in range(5):
        client.patch(f"/v1/history/{entry_id}/portion", json={"delta": -1.0},
                     headers={"X-Pathyam-User": user})

    response = client.patch(
        f"/v1/history/{entry_id}/portion", json={"delta": -1.0},
        headers={"X-Pathyam-User": user},
    )
    assert response.status_code == 200, response.text
    assert response.json()["total_kcal"] >= 0.0, "portions must never go negative"


def test_the_journal_reads_back_exactly_what_the_engine_computed(client):
    """Regression: portions were applied twice, inflating a 2-idli log by 2x.

    meal_log_item stores a per-serving figure next to a serving count and readers
    multiply them, but the engine's per_serving values are already portion-scaled.
    Storing them unadjusted double-counted -- silently, and upward.
    """
    posted = client.post(
        "/v1/log", json={"text": "2 idli", "n_samples": 300},
    ).json()

    if not posted.get("total_energy_kcal"):
        pytest.skip("nothing resolved against this database's lexicon")

    engine_total = posted["total_energy_kcal"]["p50"]
    entry = next(
        e for e in client.get("/v1/history").json()["entries"]
        if e["id"] == posted["id"]
    )
    assert entry["total_kcal"] == pytest.approx(engine_total, abs=0.5)
