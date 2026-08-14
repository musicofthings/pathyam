"""Tests for the evaluation harness itself.

A harness that silently measures the wrong thing is worse than no harness — the first
version of this one reported 100% top-1 because the golden queries were drawn from the
lexicon's alias list. These tests pin the properties that stop that recurring.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pathyam_engine.evaluation import (
    GoldenQuery, ablate, build_source, evaluate, load_lexicon, load_queries,
)
from pathyam_engine.resolution import DishResolver

EVAL_DIR = Path(__file__).resolve().parents[1] / "eval"


@pytest.fixture(scope="module")
def dishes():
    return load_lexicon(EVAL_DIR / "lexicon_south_indian.yaml")


@pytest.fixture(scope="module")
def queries():
    return load_queries(EVAL_DIR / "golden_queries.yaml")


# ------------------------------------------------------------------ loading ----

def test_lexicon_loads_and_is_substantial(dishes):
    assert len(dishes) >= 50
    for dish in dishes:
        assert dish["key"] and dish["en"]
        assert dish.get("aliases"), f"{dish['key']} has no aliases"


def test_lexicon_keys_are_unique(dishes):
    keys = [d["key"] for d in dishes]
    assert len(keys) == len(set(keys))


def test_lexicon_covers_all_four_languages(dishes):
    for lang in ("ta", "te", "ml", "kn"):
        covered = sum(1 for d in dishes if d.get(lang))
        assert covered >= 8, f"only {covered} dishes have a {lang} name"


def test_queries_reference_real_lexicon_keys(dishes, queries):
    keys = {d["key"] for d in dishes}
    for gq in queries:
        for key in gq.acceptable:
            assert key in keys, f"query {gq.q!r} expects unknown key {key!r}"


def test_golden_set_includes_negative_cases(queries):
    """A golden set of only winnable queries measures nothing."""
    absent = [q for q in queries if q.expect is None and not q.accept]
    ambiguous = [q for q in queries if q.confirm and q.acceptable]
    assert len(absent) >= 5
    assert len(ambiguous) >= 4


# ------------------------------------------------------------------ holdout ----

def test_holdout_removes_aliases_but_keeps_canonical_names(dishes):
    full = build_source(dishes)
    held = build_source(dishes, holdout=["dosai", "thosai", "idly"])
    assert len(held.entries) == len(full.entries) - 3

    surfaces = {e.surface for e in held.entries}
    assert "Dosa, plain" in surfaces, "canonical English name must survive holdout"
    assert "தோசை" in surfaces, "canonical native name must survive holdout"
    assert "dosai" not in surfaces


def test_holdout_materially_changes_the_score(dishes, queries):
    """The guard against the harness silently measuring dictionary lookup.

    With aliases present nearly every query is an exact match. If these two numbers
    are equal, the holdout has stopped working and the eval is meaningless.
    """
    full = evaluate(DishResolver(build_source(dishes)), queries)
    held = evaluate(
        DishResolver(build_source(dishes, holdout=[q.q for q in queries])), queries
    )
    assert full.top1 > held.top1 + 0.05, (
        f"holdout barely changed the score ({full.top1:.3f} vs {held.top1:.3f}) — "
        "the golden queries may have leaked into the lexicon"
    )


def test_catalogued_lexicon_resolves_almost_everything(dishes, queries):
    report = evaluate(DishResolver(build_source(dishes)), queries)
    assert report.top1 >= 0.95


# ------------------------------------------------------------------ metrics ----

def test_held_out_performance_does_not_regress(dishes, queries):
    """Regression guard. Raise these numbers when the resolver improves."""
    held = build_source(dishes, holdout=[q.q for q in queries])
    report = evaluate(DishResolver(held), queries)
    assert report.top1 >= 0.80, f"top-1 regressed to {report.top1:.3f}"
    assert report.top5 >= 0.88, f"top-5 regressed to {report.top5:.3f}"
    assert report.mrr >= 0.84


def test_abstention_catches_every_case_marked_should_ask(dishes, queries):
    held = build_source(dishes, holdout=[q.q for q in queries])
    report = evaluate(DishResolver(held), queries)
    stats = report.abstention()
    assert stats["expected_ask_recall"] == 1.0
    # Confident mistakes are the ones that reach the user unchallenged.
    assert stats["silent_errors"] <= 0.05


def test_absent_dishes_are_never_silently_logged(dishes, queries):
    held = build_source(dishes, holdout=[q.q for q in queries])
    report = evaluate(DishResolver(held), queries)
    absent = [o for o in report.outcomes
              if o.query.expect is None and not o.query.accept]
    for outcome in absent:
        assert outcome.predicted is None or outcome.asked_for_confirmation, \
            f"{outcome.query.q!r} would have been logged as {outcome.predicted!r}"


def test_report_serialises(dishes, queries):
    report = evaluate(DishResolver(build_source(dishes)), queries)
    d = report.as_dict()
    assert {"top1", "top5", "mrr", "by_category", "abstention"} <= set(d)


# ----------------------------------------------------------------- ablation ----

def test_ablation_shows_each_component_contributing(dishes, queries):
    """Both retrieval components must earn their place, measurably."""
    reports = {r.label: r for r in ablate(dishes, queries)}
    baseline = reports["baseline"].top1
    assert reports["no phonetic folding"].top1 < baseline
    assert reports["no word containment"].top1 < baseline
    assert reports["neither (raw trigram only)"].top1 < baseline - 0.03


def test_ablation_restores_module_state(dishes, queries):
    from pathyam_engine.resolution import sources

    before = (sources._PHONETIC_DISCOUNT, sources._WORD_MATCH_DISCOUNT)
    ablate(dishes, queries)
    assert (sources._PHONETIC_DISCOUNT, sources._WORD_MATCH_DISCOUNT) == before


# -------------------------------------------------------- known failure mode ----

def test_zero_overlap_synonyms_are_the_documented_residual_failure(dishes, queries):
    """Cross-language names sharing no substring with the target cannot be matched.

    'mosaranna' (Kannada) and 'curd rice' have no orthographic overlap whatsoever.
    No string-similarity method can bridge that — it needs either an alias row or
    real semantic knowledge. This test pins the finding so that a future change
    claiming to fix it has to prove it.
    """
    held = build_source(dishes, holdout=[q.q for q in queries])
    resolver = DishResolver(held)
    for query in ["mosaranna", "daddojanam", "chammanthi", "huli"]:
        items = resolver.resolve_text(query)
        best = items[0].best if items else None
        assert best is None or best.pathyam_id != _expected(queries, query), (
            f"{query!r} now resolves correctly — if that is a real improvement, "
            "update this test and the recommendation in RESOLUTION_EVAL.md"
        )


def _expected(queries: list[GoldenQuery], text: str) -> str | None:
    for gq in queries:
        if gq.q == text:
            return gq.expect
    return None
