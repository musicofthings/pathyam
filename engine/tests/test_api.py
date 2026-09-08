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
    _consent(client, "b0000000-0000-0000-0000-0000000000b1", "cgm_telemetry")
    t_resp = client.post("/v1/cgt/telemetry", headers={
        "X-Pathyam-User": "b0000000-0000-0000-0000-0000000000b1"}, json={
        "readings": [{"timestamp": "2026-08-14T08:30:00Z", "glucose_mg_dl": 98.5, "trend_arrow": "flat"}]
    })
    assert t_resp.status_code == 200
    # "count" was the echoed batch size and said nothing about persistence; the
    # response now reports what was actually stored.
    assert t_resp.json()["accepted"] == 1
    assert t_resp.json()["total_stored"] >= 1

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
    # The curve must never be presented without the caveat that it is illustrative.
    assert cgt["is_validated"] is False
    assert cgt["disclaimer"]



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


def _consent(client, user, *purposes):
    """Grant consent for a development-identity user.

    Writes are refused without it, deliberately: a user row created implicitly on
    first write has never been shown a notice. Tests grant it explicitly, which is
    what a real client does at sign-up.
    """
    for purpose in purposes:
        response = client.post(f"/v1/auth/consent/{purpose}",
                               headers={"X-Pathyam-User": user})
        assert response.status_code == 200, response.text


# ----------------------------------------------------------- meal journal ----
#
# The journal used to be a module-level Python list: history vanished on restart and
# every caller shared one log. These tests pin the two properties that fixes.


def test_a_logged_meal_is_written_to_the_database_not_process_memory(client):
    """The point of the change: the row is in Postgres, reachable without the app."""
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


def test_a_second_client_does_not_close_the_first_ones_pool(client):
    """Overlapping lifespans share one pool; only the last exit closes it.

    The pool was a module global, so entering the lifespan again -- which is all
    constructing a second TestClient does -- replaced it, and the inner teardown
    then closed the pool the outer client was still serving requests from. Every
    subsequent request on `client` failed. It is reference-counted on app.state now,
    so this reads as it should: both clients work, and the outer one survives the
    inner one being torn down.
    """
    from fastapi.testclient import TestClient
    from pathyam_api.main import app

    with TestClient(app) as second:
        assert second.get("/v1/health").status_code == 200
        assert client.get("/v1/health").status_code == 200

    # The inner client is gone; the outer one must still hold a live pool.
    assert client.get("/v1/health").status_code == 200


def test_one_users_journal_is_invisible_to_another(client):
    """user_id is a real column, not decoration."""
    alice = "11111111-1111-1111-1111-111111111111"
    bob = "22222222-2222-2222-2222-222222222222"
    _consent(client, alice, "core_service")

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
    _consent(client, owner, "core_service")

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
    _consent(client, user, "core_service")
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
    _consent(client, user, "core_service")
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


# --------------------------------------------------------- CGM telemetry ----
#
# /v1/cgt/telemetry used to echo its input and store nothing, so no glucose data
# existed anywhere. These pin that it persists, that replays do not double-count,
# and that readings can be paired back to the meal that preceded them.


def _readings(start="2026-03-01T08:00:00+00:00", n=3, first=95.0):
    import datetime

    base = datetime.datetime.fromisoformat(start)
    return [
        {
            "timestamp": (base + datetime.timedelta(minutes=15 * i)).isoformat(),
            "glucose_mg_dl": first + 10.0 * i,
            "trend_arrow": "rising",
        }
        for i in range(n)
    ]


def test_glucose_readings_are_stored_not_echoed(client):
    user = "a0000000-0000-0000-0000-0000000000a1"
    _consent(client, user, "cgm_telemetry")
    body = client.post(
        "/v1/cgt/telemetry", json={"readings": _readings()},
        headers={"X-Pathyam-User": user},
    ).json()

    assert body["accepted"] == 3
    assert body["duplicates"] == 0
    assert body["total_stored"] == 3


def test_a_replayed_batch_updates_rather_than_duplicating(client):
    """Sensors resend on reconnect; a doubled reading would bias any fitted model."""
    user = "a0000000-0000-0000-0000-0000000000a2"
    _consent(client, user, "cgm_telemetry")
    payload = {"readings": _readings(start="2026-03-02T08:00:00+00:00")}

    first = client.post("/v1/cgt/telemetry", json=payload,
                        headers={"X-Pathyam-User": user}).json()
    replay = client.post("/v1/cgt/telemetry", json=payload,
                         headers={"X-Pathyam-User": user}).json()

    assert first["accepted"] == 3
    assert replay["accepted"] == 0
    assert replay["duplicates"] == 3
    assert replay["total_stored"] == 3, "a replay must not grow the trace"


def test_one_users_readings_are_invisible_to_another(client):
    alice = "a0000000-0000-0000-0000-0000000000a3"
    bob = "a0000000-0000-0000-0000-0000000000a4"
    _consent(client, alice, "cgm_telemetry")
    _consent(client, bob, "cgm_telemetry")

    client.post("/v1/cgt/telemetry",
                json={"readings": _readings(start="2026-03-03T08:00:00+00:00")},
                headers={"X-Pathyam-User": alice})
    bob_body = client.post(
        "/v1/cgt/telemetry",
        json={"readings": _readings(start="2026-03-03T08:00:00+00:00")},
        headers={"X-Pathyam-User": bob},
    ).json()

    # Same timestamps, different user: these are separate traces, not duplicates.
    assert bob_body["accepted"] == 3
    assert bob_body["total_stored"] == 3


def test_readings_pair_back_to_the_meal_that_preceded_them(client):
    """The join a fitted CGT model would be trained against."""
    import datetime

    user = "a0000000-0000-0000-0000-0000000000a5"
    _consent(client, user, "core_service", "cgm_telemetry")
    logged = client.post(
        "/v1/log", json={"text": "2 idli", "n_samples": 120},
        headers={"X-Pathyam-User": user},
    ).json()
    meal_id = logged["id"]

    consumed = datetime.datetime.fromisoformat(
        next(e for e in client.get("/v1/history", headers={"X-Pathyam-User": user})
             .json()["entries"] if e["id"] == meal_id)["consumed_at"]
    )
    # Inside the 3h window, and one well outside it.
    inside = [
        {"timestamp": (consumed + datetime.timedelta(minutes=m)).isoformat(),
         "glucose_mg_dl": 95.0 + m, "trend_arrow": "rising"}
        for m in (15, 45, 90)
    ]
    outside = [{"timestamp": (consumed + datetime.timedelta(hours=5)).isoformat(),
                "glucose_mg_dl": 99.0, "trend_arrow": "flat"}]
    client.post("/v1/cgt/telemetry", json={"readings": inside + outside},
                headers={"X-Pathyam-User": user})

    paired = client.get(f"/v1/cgt/postprandial/{meal_id}",
                        headers={"X-Pathyam-User": user}).json()

    assert paired["count"] == 3, "only readings inside the 3h window belong to the meal"
    minutes = [r["minutes_since_meal"] for r in paired["readings"]]
    assert minutes == sorted(minutes)
    assert all(0 <= m < 180 for m in minutes)


def test_an_out_of_range_reading_is_rejected(client):
    """Outside 40-450 mg/dL is a sensor error, not a measurement.

    422 comes from request validation, before the consent check — a malformed
    payload should not need consent to be told it is malformed.
    """
    response = client.post(
        "/v1/cgt/telemetry",
        json={"readings": [{"timestamp": "2026-03-04T08:00:00+00:00",
                            "glucose_mg_dl": 900.0}]},
        headers={"X-Pathyam-User": "a0000000-0000-0000-0000-0000000000a6"},
    )
    assert response.status_code == 422


# ------------------------------------------------------------------ auth ----

def test_register_login_and_scoped_data(client):
    """The whole point: a session token, not a self-asserted header."""
    creds = {"email": "auth-test-1@example.com", "password": "a-long-enough-password"}

    registered = client.post("/v1/auth/register", json=creds)
    assert registered.status_code == 201, registered.text
    token = registered.json()["access_token"]
    user_id = registered.json()["user_id"]
    auth = {"Authorization": f"Bearer {token}"}

    assert client.get("/v1/auth/me", headers=auth).json()["user_id"] == user_id

    client.post("/v1/log", json={"text": "2 idli", "n_samples": 120}, headers=auth)
    assert client.get("/v1/history", headers=auth).json()["count"] >= 1

    # A second account sees none of it.
    other = client.post("/v1/auth/register", json={
        "email": "auth-test-2@example.com", "password": "another-long-password"}).json()
    other_auth = {"Authorization": f"Bearer {other['access_token']}"}
    assert client.get("/v1/history", headers=other_auth).json()["count"] == 0


def test_a_wrong_password_is_rejected_without_saying_which_field_was_wrong(client):
    client.post("/v1/auth/register", json={
        "email": "auth-test-3@example.com", "password": "the-real-password"})

    wrong_password = client.post("/v1/auth/login", json={
        "email": "auth-test-3@example.com", "password": "not-the-password"})
    unknown_email = client.post("/v1/auth/login", json={
        "email": "nobody-here@example.com", "password": "not-the-password"})

    assert wrong_password.status_code == 401
    assert unknown_email.status_code == 401
    # Identical message: login must not reveal which addresses are registered.
    assert wrong_password.json()["detail"] == unknown_email.json()["detail"]


def test_a_bad_or_revoked_token_is_refused(client):
    creds = {"email": "auth-test-4@example.com", "password": "yet-another-password"}
    token = client.post("/v1/auth/register", json=creds).json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}

    assert client.get("/v1/auth/me", headers=auth).status_code == 200
    assert client.post("/v1/auth/logout", headers=auth).json()["sessions_revoked"] == 1
    assert client.get("/v1/auth/me", headers=auth).status_code == 401

    assert client.get(
        "/v1/auth/me", headers={"Authorization": "Bearer not-a-real-token"}
    ).status_code == 401


def test_logout_everywhere_revokes_every_session(client):
    """Why sessions are opaque rows and not JWTs: this has to actually work."""
    creds = {"email": "auth-test-5@example.com", "password": "a-fifth-long-password"}
    client.post("/v1/auth/register", json=creds)

    first = client.post("/v1/auth/login", json=creds).json()["access_token"]
    second = client.post("/v1/auth/login", json=creds).json()["access_token"]

    revoked = client.post(
        "/v1/auth/logout?everywhere=true",
        headers={"Authorization": f"Bearer {second}"},
    ).json()["sessions_revoked"]
    assert revoked >= 2

    for token in (first, second):
        assert client.get(
            "/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
        ).status_code == 401


def test_the_unverified_dev_header_is_refused_in_production(client, monkeypatch):
    """A self-asserted identity header alongside real auth is a way in."""
    monkeypatch.setenv("PATHYAM_ENV", "production")
    response = client.get(
        "/v1/history",
        headers={"X-Pathyam-User": "00000000-0000-0000-0000-000000000009"},
    )
    assert response.status_code == 401
    assert "authentication required" in response.json()["detail"]


# ------------------------------------------------------ consent enforcement ----

def test_a_meal_log_is_refused_without_core_service_consent(client):
    user = "d0000000-0000-0000-0000-0000000000d1"
    response = client.post("/v1/log", json={"text": "2 idli", "n_samples": 120},
                           headers={"X-Pathyam-User": user})

    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["purpose"] == "core_service"
    # The message must say how to fix it: a bare 403 on a meal log is
    # indistinguishable from a bug.
    assert detail["grant_with"] == "POST /v1/auth/consent/core_service"


def test_glucose_telemetry_is_refused_without_its_own_consent(client):
    """core_service is not enough. A continuous trace is granted separately."""
    user = "d0000000-0000-0000-0000-0000000000d2"
    _consent(client, user, "core_service")

    refused = client.post(
        "/v1/cgt/telemetry", json={"readings": _readings()},
        headers={"X-Pathyam-User": user},
    )
    assert refused.status_code == 403
    assert refused.json()["detail"]["purpose"] == "cgm_telemetry"

    _consent(client, user, "cgm_telemetry")
    accepted = client.post(
        "/v1/cgt/telemetry", json={"readings": _readings()},
        headers={"X-Pathyam-User": user},
    )
    assert accepted.status_code == 200
    assert accepted.json()["accepted"] == 3


def test_registering_records_consent_for_the_required_purposes(client):
    """A real client consents at sign-up; nothing implicit is assumed later."""
    session = client.post("/v1/auth/register", json={
        "email": "consent-test@example.com",
        "password": "a-sufficiently-long-password",
    }).json()
    auth = {"Authorization": f"Bearer {session['access_token']}"}

    assert client.post("/v1/log", json={"text": "2 idli", "n_samples": 120},
                       headers=auth).status_code == 200
    # But the optional purpose is NOT granted by signing up.
    assert client.post("/v1/cgt/telemetry", json={"readings": _readings()},
                       headers=auth).status_code == 403


def test_granting_the_same_consent_twice_is_not_an_error(client):
    """user_consent_active_idx is unique while active; a double tap must not 500."""
    user = "d0000000-0000-0000-0000-0000000000d3"
    for _ in range(3):
        response = client.post("/v1/auth/consent/core_service",
                               headers={"X-Pathyam-User": user})
        assert response.status_code == 200
        assert response.json()["granted"] is True


# --------------------------------------------------------- auth rate limit ----

def test_repeated_login_attempts_from_one_address_are_throttled(client):
    """Per-account lockout does not cover password spraying: one attempt against
    each of many accounts never trips a per-account counter."""
    from pathyam_api.main import _auth_limiter
    from pathyam_api.ratelimit import AUTH_LIMIT

    _auth_limiter.reset()
    try:
        statuses = [
            client.post("/v1/auth/login", json={
                "email": f"spray-{i}@example.com", "password": "wrong-password-here",
            }).status_code
            for i in range(AUTH_LIMIT.requests + 3)
        ]
    finally:
        _auth_limiter.reset()

    assert statuses[0] == 401, "the first attempts fail on credentials, not the limit"
    assert 429 in statuses, "sustained attempts from one address must be throttled"
    # Every account tried was different, so nothing here trips the per-account lockout.
    assert statuses.index(429) >= AUTH_LIMIT.requests


def test_a_throttled_response_says_when_to_retry(client):
    from pathyam_api.main import _auth_limiter
    from pathyam_api.ratelimit import AUTH_LIMIT

    _auth_limiter.reset()
    try:
        response = None
        for _ in range(AUTH_LIMIT.requests + 2):
            response = client.post("/v1/auth/login", json={
                "email": "retry-after@example.com", "password": "wrong-password-here"})
        assert response.status_code == 429
        assert int(response.headers["Retry-After"]) > 0
    finally:
        _auth_limiter.reset()


def test_the_rate_limit_does_not_block_ordinary_api_use(client):
    """It is on the credential endpoints only; logging meals is not throttled here."""
    from pathyam_api.main import _auth_limiter
    from pathyam_api.ratelimit import AUTH_LIMIT

    _auth_limiter.reset()
    user = "e0000000-0000-0000-0000-0000000000e1"
    _consent(client, user, "core_service")
    try:
        for _ in range(AUTH_LIMIT.requests + 5):
            response = client.get("/v1/history", headers={"X-Pathyam-User": user})
            assert response.status_code == 200
    finally:
        _auth_limiter.reset()


# ------------------------------------------------- third-party vision consent ----
#
# Both vision endpoints send a photograph the user took of their own meal out of
# Pathyam to an external model provider. Until db/019 neither required
# authentication at all, so there was no subject whose consent could be checked and
# nothing recorded that the photo had left the system.
#
# These run with PATHYAM_MOCK_VISION so no network call happens. The mock is
# labelled model_version="mock" and never leaves the process, which is exactly why
# it is safe to use to test the gate that guards real disclosure.

_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 4000


@pytest.fixture
def mock_vision(monkeypatch):
    monkeypatch.setenv("PATHYAM_MOCK_VISION", "1")
    yield


def test_a_meal_photo_is_refused_without_vision_consent(client, mock_vision):
    """core_service is not enough: this sends the photo to a third party."""
    user = "d0000000-0000-0000-0000-0000000000e1"
    _consent(client, user, "core_service")

    refused = client.post(
        "/v1/vision/resolve",
        files={"file": ("meal.jpg", _JPEG, "image/jpeg")},
        headers={"X-Pathyam-User": user},
    )
    assert refused.status_code == 403
    detail = refused.json()["detail"]
    assert detail["purpose"] == "vision_third_party"
    assert detail["grant_with"] == "POST /v1/auth/consent/vision_third_party"


def test_a_meal_photo_is_accepted_once_vision_consent_is_granted(client, mock_vision):
    user = "d0000000-0000-0000-0000-0000000000e2"
    _consent(client, user, "core_service", "vision_third_party")

    accepted = client.post(
        "/v1/vision/resolve",
        files={"file": ("meal.jpg", _JPEG, "image/jpeg")},
        headers={"X-Pathyam-User": user},
    )
    assert accepted.status_code == 200, accepted.text
    body = accepted.json()
    assert body["model_version"] == "mock", "the mock must stay identifiable in the response"
    # The mock never leaves this process, so nothing was disclosed to anyone.
    assert body["provider_may_train_on_input"] is False


def test_perception_analyze_is_gated_by_the_same_purpose(client, mock_vision):
    """The other way an image reaches a provider. Gating one and not the other
    would leave the disclosure reachable through a different door."""
    import base64

    user = "d0000000-0000-0000-0000-0000000000e3"
    _consent(client, user, "core_service")
    payload = {"image_base64": base64.b64encode(_JPEG).decode()}

    refused = client.post("/v1/perception/analyze", json=payload,
                          headers={"X-Pathyam-User": user})
    assert refused.status_code == 403
    assert refused.json()["detail"]["purpose"] == "vision_third_party"

    _consent(client, user, "vision_third_party")
    assert client.post("/v1/perception/analyze", json=payload,
                       headers={"X-Pathyam-User": user}).status_code == 200


def test_vision_requires_authentication_at_all(client, mock_vision, monkeypatch):
    """The endpoint used to take no user. Anyone who could reach it could have a
    photograph forwarded to a third party with no subject and no record."""
    monkeypatch.setenv("PATHYAM_ALLOW_DEV_IDENTITY", "0")

    anonymous = client.post(
        "/v1/vision/resolve",
        files={"file": ("meal.jpg", _JPEG, "image/jpeg")},
    )
    assert anonymous.status_code == 401


def test_signing_up_does_not_grant_vision_consent(client, mock_vision):
    """Optional and sensitive, like cgm_telemetry. Sign-up covers neither."""
    session = client.post("/v1/auth/register", json={
        "email": "vision-consent@example.com",
        "password": "a-sufficiently-long-password",
    }).json()
    auth = {"Authorization": f"Bearer {session['access_token']}"}

    assert client.post("/v1/log", json={"text": "2 idli", "n_samples": 120},
                       headers=auth).status_code == 200
    refused = client.post("/v1/vision/resolve",
                          files={"file": ("meal.jpg", _JPEG, "image/jpeg")},
                          headers=auth)
    assert refused.status_code == 403
    assert refused.json()["detail"]["purpose"] == "vision_third_party"


def test_vision_consent_is_registered_as_optional_not_required(client):
    """Consent that must be given to use the product at all is not freely given.

    Meals can be logged as text, so photography is a convenience on top and the
    purpose is optional.
    """
    import psycopg

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("""SELECT is_required_for_service, description_en
                         FROM app.consent_purpose
                        WHERE purpose_key = 'vision_third_party'""")
        row = cur.fetchone()

    assert row is not None, "db/019 did not register the purpose"
    required, description = row
    assert required is False
    # The notice has to say the photo leaves Pathyam; that is the whole disclosure.
    assert "leaves Pathyam" in description
    assert "text instead" in description


# ------------------------------------------------------ dashboard glucose card ----
#
# The dashboard card used to assert a sensor reading that did not exist. Three
# separate fabrications, stacked:
#
#   * "Patch Connected" was a hardcoded <span> — nothing checked for readings.
#   * daily_peak_glucose_mg_dl was `max([...], default=95.0)`, so a user with no
#     meals and no sensor saw 95 mg/dL as though it had been measured.
#   * when meals DID exist, the number was _illustrative_peak() over their macros —
#     an unfitted prediction — displayed under a "CGT Sensor Spike" heading beside
#     "Postprandial trajectory optimal", a clinical judgement with nothing behind it.
#
# The real readings were in app.cgm_reading the whole time and nothing read them.


def test_the_dashboard_reports_no_glucose_when_no_sensor_reported(client):
    """The 95.0 default is gone. Absent data renders as absent."""
    user = "d0000000-0000-0000-0000-0000000000f1"
    _consent(client, user, "core_service")

    summary = client.get("/v1/dashboard/summary",
                         headers={"X-Pathyam-User": user}).json()

    assert summary["daily_peak_glucose_mg_dl"] is None
    assert summary["glucose_readings_today"] == 0


def test_logging_a_meal_does_not_manufacture_a_glucose_reading(client):
    """A meal is not a measurement.

    This is the substantive change: the figure used to be the illustrative CGT
    curve over the meal's macros, shown as a sensor spike. Eating without wearing a
    sensor must produce no glucose number at all.
    """
    user = "d0000000-0000-0000-0000-0000000000f2"
    _consent(client, user, "core_service")
    logged = client.post("/v1/log", json={"text": "2 idli", "n_samples": 120},
                         headers={"X-Pathyam-User": user})
    assert logged.status_code == 200

    summary = client.get("/v1/dashboard/summary",
                         headers={"X-Pathyam-User": user}).json()

    # The meal is recorded (energy depends on which templates this database carries,
    # so the count is what to assert here, not kcal).
    assert summary["meals_logged_count"] >= 1
    assert summary["daily_peak_glucose_mg_dl"] is None
    assert summary["glucose_readings_today"] == 0


def test_the_dashboard_reports_the_measured_peak_not_the_predicted_one(client):
    """With a sensor, the number is the maximum of app.cgm_reading."""
    import datetime

    user = "d0000000-0000-0000-0000-0000000000f3"
    _consent(client, user, "core_service", "cgm_telemetry")

    now = datetime.datetime.now(datetime.timezone.utc)
    readings = [
        {"timestamp": (now - datetime.timedelta(minutes=m)).isoformat(),
         "glucose_mg_dl": v, "trend_arrow": "flat"}
        for m, v in [(90, 101.0), (60, 187.0), (30, 118.0)]
    ]
    assert client.post("/v1/cgt/telemetry", json={"readings": readings},
                       headers={"X-Pathyam-User": user}).status_code == 200

    # A meal too, so the illustrative curve has macros to work from. If the endpoint
    # regressed to that curve, it would not produce 187.
    client.post("/v1/log", json={"text": "2 idli", "n_samples": 120},
                headers={"X-Pathyam-User": user})

    summary = client.get("/v1/dashboard/summary",
                         headers={"X-Pathyam-User": user}).json()

    assert summary["daily_peak_glucose_mg_dl"] == 187.0
    assert summary["glucose_readings_today"] == 3


def test_one_users_glucose_peak_is_invisible_to_another(client):
    """The peak is per-user, like every other row in app.*."""
    import datetime

    owner = "d0000000-0000-0000-0000-0000000000f4"
    other = "d0000000-0000-0000-0000-0000000000f5"
    _consent(client, owner, "core_service", "cgm_telemetry")
    _consent(client, other, "core_service", "cgm_telemetry")

    now = datetime.datetime.now(datetime.timezone.utc)
    client.post("/v1/cgt/telemetry", headers={"X-Pathyam-User": owner}, json={
        "readings": [{"timestamp": now.isoformat(), "glucose_mg_dl": 201.0,
                      "trend_arrow": "flat"}]})

    theirs = client.get("/v1/dashboard/summary",
                        headers={"X-Pathyam-User": other}).json()
    assert theirs["daily_peak_glucose_mg_dl"] is None


def test_the_dashboard_card_makes_no_clinical_judgement(client):
    """"Postprandial trajectory optimal" was static text under the glucose figure.

    A reassurance about someone's glucose trajectory is a clinical claim, and it was
    rendered whatever the data said — including when there was none.
    """
    page = client.get("/").text
    assert "trajectory optimal" not in page.replace("Postprandial trajectory optimal\" —", "")
    assert "<span>CGT Sensor Spike</span>" not in page
    assert "<span>Measured Glucose</span>" in page


# ------------------------------------------------------------ password reset ----
#
# Before this, an account whose password was forgotten was unrecoverable.


def _forgot(client, email):
    return client.post("/v1/auth/password/forgot", json={"email": email})


def _reset_token(monkeypatch_free_mailer):
    """The token the console mailer captured, pulled out of the message body."""
    import re
    body = monkeypatch_free_mailer.sent[-1]["body"]
    match = re.search(r"\n\s{2,}(\S{20,})\n", body)
    assert match, f"no token found in:\n{body}"
    return match.group(1)


@pytest.fixture
def mailer(monkeypatch):
    """Console mailer we can read, plus a cleared rate limiter.

    The limiter is process-global by design (see ratelimit.py — it is an in-process
    brake, not the edge limit), so it accumulates across the whole test module. A
    reset flow is several credential calls, which is enough to trip the 10/60s
    allowance on its own once earlier tests have spent some of it.
    """
    from pathyam_api import mailer as mailer_mod
    from pathyam_api import main as main_mod

    main_mod._auth_limiter._hits.clear()
    sender = mailer_mod.ConsoleMailer()
    monkeypatch.setattr(main_mod, "_MAILER", sender)
    monkeypatch.delenv("PATHYAM_APP_URL", raising=False)
    return sender


def _new_account(client, email="reset-me@example.com", password="original-password-1"):
    r = client.post("/v1/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    return r.json()


def test_a_forgotten_password_can_be_reset_and_signs_you_in(client, mailer):
    email = "reset-flow@example.com"
    _new_account(client, email)

    assert _forgot(client, email).status_code == 202
    token = _reset_token(mailer)

    reset = client.post("/v1/auth/password/reset",
                        json={"token": token, "new_password": "a-brand-new-password"})
    assert reset.status_code == 200, reset.text
    session = reset.json()
    assert session["access_token"]

    # The returned session works, and the new password works.
    assert client.get("/v1/auth/me", headers={
        "Authorization": f"Bearer {session['access_token']}"}).status_code == 200
    assert client.post("/v1/auth/login", json={
        "email": email, "password": "a-brand-new-password"}).status_code == 200
    assert client.post("/v1/auth/login", json={
        "email": email, "password": "original-password-1"}).status_code == 401


def test_resetting_revokes_every_other_session(client, mailer):
    """The point of a reset. A reset commonly happens because someone else got in;
    leaving their session alive would make the whole exercise cosmetic."""
    email = "reset-evicts@example.com"
    intruder = _new_account(client, email)["access_token"]
    intruder_headers = {"Authorization": f"Bearer {intruder}"}
    assert client.get("/v1/auth/me", headers=intruder_headers).status_code == 200

    _forgot(client, email)
    reset = client.post("/v1/auth/password/reset", json={
        "token": _reset_token(mailer), "new_password": "a-brand-new-password"})
    assert reset.status_code == 200

    assert client.get("/v1/auth/me", headers=intruder_headers).status_code == 401, \
        "the pre-existing session survived the reset"
    # ...but the session the reset handed back is live.
    assert client.get("/v1/auth/me", headers={
        "Authorization": f"Bearer {reset.json()['access_token']}"}).status_code == 200


def test_a_reset_token_works_only_once(client, mailer):
    """It sits in an inbox, which is not a place for a reusable credential."""
    email = "reset-once@example.com"
    _new_account(client, email)
    _forgot(client, email)
    token = _reset_token(mailer)

    assert client.post("/v1/auth/password/reset", json={
        "token": token, "new_password": "first-new-password"}).status_code == 200
    replayed = client.post("/v1/auth/password/reset", json={
        "token": token, "new_password": "second-new-password"})
    assert replayed.status_code == 400
    assert "already used" in replayed.json()["detail"]


def test_requesting_a_second_reset_invalidates_the_first(client, mailer):
    """An attacker who requests a link first must not keep it alive while the real
    owner requests another."""
    email = "reset-supersede@example.com"
    _new_account(client, email)
    _forgot(client, email)
    first = _reset_token(mailer)
    _forgot(client, email)
    second = _reset_token(mailer)
    assert first != second

    assert client.post("/v1/auth/password/reset", json={
        "token": first, "new_password": "should-not-work-x"}).status_code == 400
    assert client.post("/v1/auth/password/reset", json={
        "token": second, "new_password": "should-work-fine-x"}).status_code == 200


def test_forgot_does_not_reveal_whether_an_address_has_an_account(client, mailer):
    """Otherwise this endpoint is a way to test whether someone is a Pathyam user."""
    _new_account(client, "known-user@example.com")

    known = _forgot(client, "known-user@example.com")
    unknown = _forgot(client, "definitely-not-registered@example.com")

    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()
    # And no mail was generated for the address that does not exist.
    assert len(mailer.sent) == 1
    assert mailer.sent[0]["to"] == "known-user@example.com"


def test_an_unknown_or_garbage_token_is_refused(client, mailer):
    bad = client.post("/v1/auth/password/reset", json={
        "token": "not-a-real-token-at-all", "new_password": "a-valid-password-1"})
    assert bad.status_code == 400


def test_reset_will_not_set_a_password_registration_would_have_refused(client, mailer):
    """Otherwise reset becomes the way around the minimum length."""
    email = "reset-weak@example.com"
    _new_account(client, email)
    _forgot(client, email)

    weak = client.post("/v1/auth/password/reset", json={
        "token": _reset_token(mailer), "new_password": "short"})
    assert weak.status_code == 422


def test_reset_clears_a_lockout(client, mailer):
    """A user locked out by someone else guessing at their password must be able to
    get back in. Otherwise an attacker can deny access simply by guessing wrong."""
    email = "reset-locked@example.com"
    _new_account(client, email, password="original-password-1")

    # The per-IP rate limiter (10/60s) would trip before the per-account lockout
    # (8 attempts) does, and this test is about the lockout. Cleared between steps
    # so one mechanism is exercised at a time.
    from pathyam_api import main as main_mod

    for _ in range(9):
        main_mod._auth_limiter._hits.clear()
        client.post("/v1/auth/login", json={"email": email, "password": "wrong-guess-x"})

    main_mod._auth_limiter._hits.clear()
    locked = client.post("/v1/auth/login",
                         json={"email": email, "password": "original-password-1"})
    # 429 with "too many failed attempts" is the per-account lockout (main.py maps
    # AccountLocked to 429), not the per-IP limiter — those are different brakes
    # that happen to share a status code.
    assert locked.status_code == 429, \
        f"expected the account to be locked, got {locked.status_code}: {locked.text}"
    assert "failed attempts" in locked.json()["detail"]

    main_mod._auth_limiter._hits.clear()
    _forgot(client, email)
    main_mod._auth_limiter._hits.clear()
    reset = client.post("/v1/auth/password/reset", json={
        "token": _reset_token(mailer), "new_password": "a-brand-new-password"})
    assert reset.status_code == 200

    main_mod._auth_limiter._hits.clear()
    assert client.post("/v1/auth/login", json={
        "email": email, "password": "a-brand-new-password"}).status_code == 200, \
        "the lockout survived the reset"
