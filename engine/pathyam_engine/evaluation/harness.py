"""Evaluation harness for dish resolution.

Turns "it seems to work" into numbers you can defend, and — more usefully — into an
ablation that says whether the next component is worth building.

Metrics
-------
``top1`` / ``top5``   fraction of queries whose correct dish is ranked first / in the top five.
``mrr``               mean reciprocal rank; rewards being close when not first.
``abstention``        of the queries where the resolver asked for confirmation, how many
                      would have been wrong if it had not? This is the metric that says
                      whether the confirmation threshold is calibrated or just noisy.

Slices are reported per category (native script, romanised, misspelling, ambiguous,
absent) because an aggregate number hides the failures that matter: a system that is
95% on English and 40% on Malayalam is not a 90% system for a Kerala user.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..resolution import DishResolver, InMemoryCandidateSource, LexiconEntry

__all__ = ["GoldenQuery", "QueryOutcome", "EvalReport", "load_lexicon",
           "load_queries", "build_source", "evaluate", "ablate"]


@dataclass(frozen=True)
class GoldenQuery:
    q: str
    expect: str | None
    category: str
    accept: tuple[str, ...] = ()
    confirm: bool = False
    note: str | None = None

    @property
    def acceptable(self) -> set[str]:
        keys = set(self.accept)
        if self.expect:
            keys.add(self.expect)
        return keys


@dataclass
class QueryOutcome:
    query: GoldenQuery
    predicted: str | None
    rank: int | None                  # 1-based rank of the correct dish, None if absent
    confidence: float
    margin: float
    asked_for_confirmation: bool
    method: str | None
    latency_ms: float

    @property
    def is_top1(self) -> bool:
        return self.rank == 1

    @property
    def is_top5(self) -> bool:
        return self.rank is not None and self.rank <= 5

    @property
    def correct(self) -> bool:
        """For absent dishes, correct means the user is never shown a wrong dish.

        Either no candidate cleared the retrieval floor, or the resolver flagged the
        match for confirmation — in both cases nothing is logged. Returning a weak
        candidate *and* asking is worse UX than returning nothing, but it is not a
        data error, so it is tracked separately in ``EvalReport.absent_noise``.
        """
        if self.query.expect is None and not self.query.accept:
            return self.predicted is None or self.asked_for_confirmation
        return self.is_top1


@dataclass
class EvalReport:
    outcomes: list[QueryOutcome]
    label: str = "baseline"
    lexicon_size: int = 0
    warnings: list[str] = field(default_factory=list)

    # -- headline ---------------------------------------------------------

    def _scored(self) -> list[QueryOutcome]:
        """Queries with a right answer. Absent-dish queries are scored separately."""
        return [o for o in self.outcomes if o.query.acceptable]

    @property
    def top1(self) -> float:
        scored = self._scored()
        return sum(o.is_top1 for o in scored) / len(scored) if scored else 0.0

    @property
    def top5(self) -> float:
        scored = self._scored()
        return sum(o.is_top5 for o in scored) / len(scored) if scored else 0.0

    @property
    def mrr(self) -> float:
        scored = self._scored()
        if not scored:
            return 0.0
        return sum(1.0 / o.rank if o.rank else 0.0 for o in scored) / len(scored)

    @property
    def median_latency_ms(self) -> float:
        if not self.outcomes:
            return 0.0
        values = sorted(o.latency_ms for o in self.outcomes)
        return values[len(values) // 2]

    # -- slices -----------------------------------------------------------

    def by_category(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for category in sorted({o.query.category for o in self.outcomes}):
            rows = [o for o in self.outcomes if o.query.category == category]
            scored = [o for o in rows if o.query.acceptable]
            out[category] = {
                "n": len(rows),
                "top1": (sum(o.is_top1 for o in scored) / len(scored)) if scored else None,
                "top5": (sum(o.is_top5 for o in scored) / len(scored)) if scored else None,
                "correct": sum(o.correct for o in rows) / len(rows),
            }
        return out

    # -- abstention calibration -------------------------------------------

    def abstention(self) -> dict[str, Any]:
        """Is the confirmation threshold earning its keep?

        ``useful`` — of the queries where the resolver asked, how many would have been
        wrong had it stayed silent. High is good: it asked when it needed to.
        ``silent_errors`` — of the queries where it did NOT ask, how many were wrong.
        Low is good: these are the ones that reach the user as confident mistakes.
        """
        asked = [o for o in self.outcomes if o.asked_for_confirmation]
        quiet = [o for o in self.outcomes if not o.asked_for_confirmation]
        expected_ask = [o for o in self.outcomes if o.query.confirm]
        return {
            "asked": len(asked),
            "asked_pct": len(asked) / len(self.outcomes) if self.outcomes else 0.0,
            "useful": (sum(not o.correct for o in asked) / len(asked)) if asked else None,
            "silent_errors": (sum(not o.correct for o in quiet) / len(quiet)) if quiet else None,
            "expected_ask_recall": (
                sum(o.asked_for_confirmation for o in expected_ask) / len(expected_ask)
            ) if expected_ask else None,
        }

    def absent_noise(self) -> float | None:
        """Of the dishes not in the lexicon, how often was a candidate offered anyway?

        Not a correctness failure — those items still get withheld — but every one is
        a pointless "did you mean puttu?" prompt for someone who typed "pizza".
        """
        absent = [o for o in self.outcomes
                  if o.query.expect is None and not o.query.accept]
        if not absent:
            return None
        return sum(o.predicted is not None for o in absent) / len(absent)

    def failures(self, limit: int = 25) -> list[QueryOutcome]:
        bad = [o for o in self.outcomes if not o.correct]
        bad.sort(key=lambda o: (o.rank is None, o.rank or 999))
        return bad[:limit]

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "n_queries": len(self.outcomes),
            "lexicon_size": self.lexicon_size,
            "top1": round(self.top1, 4),
            "top5": round(self.top5, 4),
            "mrr": round(self.mrr, 4),
            "median_latency_ms": round(self.median_latency_ms, 3),
            "by_category": self.by_category(),
            "abstention": self.abstention(),
        }


# ------------------------------------------------------------------- loading ----


def load_lexicon(path: str | Path) -> list[dict[str, Any]]:
    import yaml

    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)["dishes"]


def load_queries(path: str | Path) -> list[GoldenQuery]:
    import yaml

    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)["queries"]
    return [
        GoldenQuery(
            q=r["q"], expect=r.get("expect"), category=r.get("category", "uncategorised"),
            accept=tuple(r.get("accept", ())), confirm=bool(r.get("confirm", False)),
            note=r.get("note"),
        )
        for r in raw
    ]


def build_source(
    dishes: Sequence[dict[str, Any]],
    *,
    holdout: Iterable[str] = (),
) -> InMemoryCandidateSource:
    """Flatten the YAML lexicon into one entry per (dish, language, surface form).

    ``food_id`` is the dish's index and ``pathyam_id`` carries the lexicon key, so the
    harness can map a resolved candidate back to the expected key.

    ``holdout`` removes matching **alias** rows — never canonical names, or the dish
    becomes unfindable and the measurement is meaningless. This exists because the
    first version of this harness scored 100% top-1 with a 0.0-point ablation delta:
    the golden queries were drawn from the alias list, so 134 of 145 were exact string
    matches and the eval was measuring dictionary lookup. Holding the aliases out
    forces fuzzy and phonetic matching to do the work, which is the real-world case —
    you can never enumerate every romanisation your users will type.
    """
    from ..resolution.trigram import normalize

    held = {normalize(h) for h in holdout}
    entries: list[LexiconEntry] = []

    for index, dish in enumerate(dishes, start=1):
        key, name_en = dish["key"], dish["en"]
        template_id = index if dish.get("has_template") else None
        group = dish.get("group")

        is_base = bool(dish.get("base"))

        # Canonical names always stay.
        entries.append(LexiconEntry(index, key, name_en, "en", name_en, True,
                                    template_id=template_id, food_group=group,
                                    is_base=is_base))
        for lang in ("ta", "te", "ml", "kn"):
            if dish.get(lang):
                entries.append(LexiconEntry(index, key, name_en, lang, dish[lang], True,
                                            template_id=template_id, food_group=group,
                                            is_base=is_base))

        for alias in dish.get("aliases", ()):
            if alias.lower() == name_en.lower():
                continue
            if normalize(alias) in held:
                continue
            entries.append(LexiconEntry(index, key, name_en, "en", alias, False,
                                        template_id=template_id, food_group=group,
                                        is_base=is_base))
    return InMemoryCandidateSource(entries)


# ---------------------------------------------------------------- evaluation ----


def evaluate(
    resolver: DishResolver,
    queries: Iterable[GoldenQuery],
    *,
    label: str = "baseline",
    lexicon_size: int = 0,
    limit: int = 5,
    preferred_lang: str | None = None,
    # None means "use the resolver's own default". Hard-coding 0.20 here silently
    # overrode the tuned production floor, so the CLI reported numbers for a
    # configuration that no longer shipped.
    min_similarity: float | None = None,
) -> EvalReport:
    extra = {} if min_similarity is None else {"min_similarity": min_similarity}
    outcomes = [
        _score_query(resolver, gq, limit=limit, preferred_lang=preferred_lang,
                     extra=extra)
        for gq in queries
    ]
    return EvalReport(outcomes=outcomes, label=label, lexicon_size=lexicon_size)


def _score_query(
    resolver: DishResolver,
    gq: GoldenQuery,
    *,
    limit: int,
    preferred_lang: str | None,
    extra: dict[str, Any],
) -> QueryOutcome:
    started = time.perf_counter()
    items = resolver.resolve_text(gq.q, limit=limit, preferred_lang=preferred_lang,
                                  **extra)
    elapsed = (time.perf_counter() - started) * 1000.0

    # Multi-item lines are scored on the first item; the golden set is written
    # so that the first item is always the one under test.
    item = items[0] if items else None
    candidates = item.candidates if item else []

    rank = None
    for position, candidate in enumerate(candidates, start=1):
        if candidate.pathyam_id in gq.acceptable:
            rank = position
            break

    return QueryOutcome(
        query=gq,
        predicted=candidates[0].pathyam_id if candidates else None,
        rank=rank,
        confidence=item.confidence if item else 0.0,
        margin=item.margin if item else 0.0,
        asked_for_confirmation=item.needs_confirmation if item else True,
        method=candidates[0].method if candidates else None,
        latency_ms=elapsed,
    )


def evaluate_leave_one_out(
    dishes: Sequence[dict[str, Any]],
    queries: Sequence[GoldenQuery],
    *,
    label: str = "leave-one-out",
    limit: int = 5,
    preferred_lang: str | None = None,
    min_similarity: float | None = None,
) -> EvalReport:
    """Hold out only the query's OWN alias, leaving the dish's other spellings in.

    The global holdout used by :func:`ablate` removes every golden query string at
    once. For a well-covered dish that removes all of its spellings simultaneously:
    ``dosa_plain`` lists nine surface forms and six of them (dosa, dosai, thosai,
    dose, dosey, dhosa) are golden queries, so the dish is left with no bare name at
    all while ``dosa_rava`` keeps "ravai dosai" containing the queried token exactly.
    A one-word query then cannot outrank a two-word sibling however the scoring is
    tuned, and the failure is an artifact of the ablation rather than something a
    user would ever hit -- in production "dosai" is a catalogued alias and resolves
    exactly.

    Leave-one-out models the real case: an unseen spelling of a dish whose *other*
    spellings are known. It is the number to tune ranking against. The global
    holdout remains useful as a deliberately harsher floor, so both are reported.

    Canonical and native-script names are never held out, matching
    :func:`build_source` -- removing those makes a dish unfindable rather than
    unfamiliar.
    """
    from ..resolution.trigram import normalize

    extra = {} if min_similarity is None else {"min_similarity": min_similarity}
    full = build_source(dishes)
    outcomes: list[QueryOutcome] = []

    for gq in queries:
        held = normalize(gq.q)
        entries = [
            e for e in full.entries
            if e.is_primary or normalize(e.surface) != held
        ]
        resolver = DishResolver(InMemoryCandidateSource(entries))
        outcomes.append(_score_query(resolver, gq, limit=limit,
                                     preferred_lang=preferred_lang, extra=extra))

    return EvalReport(outcomes=outcomes, label=label,
                      lexicon_size=len(full.entries))


def ablate(
    dishes: Sequence[dict[str, Any]],
    queries: Sequence[GoldenQuery],
    *,
    holdout: bool = True,
) -> list[EvalReport]:
    """Measure what each retrieval component actually contributes.

    Ablation is the point of this harness. Adding vector retrieval before knowing how
    much headroom trigram and phonetic folding leave is how a team spends a month on a
    component that buys two points.

    Runs held-out by default: with the alias rows present, every query is an exact
    match and every ablation reports an identical 100%, which tells you nothing.
    """
    from ..resolution import sources as sources_module

    source = build_source(
        dishes, holdout=[q.q for q in queries] if holdout else ()
    )
    reports: list[EvalReport] = []

    settings = [
        ("baseline", None, None),
        ("no phonetic folding", 0.0, None),
        ("no word containment", None, 0.0),
        ("neither (raw trigram only)", 0.0, 0.0),
    ]

    original_phonetic = sources_module._PHONETIC_DISCOUNT
    original_word = sources_module._WORD_MATCH_DISCOUNT
    try:
        for label, phonetic, word in settings:
            sources_module._PHONETIC_DISCOUNT = (
                original_phonetic if phonetic is None else phonetic
            )
            sources_module._WORD_MATCH_DISCOUNT = (
                original_word if word is None else word
            )
            reports.append(evaluate(
                DishResolver(source), queries, label=label, lexicon_size=len(source.entries)
            ))
    finally:
        sources_module._PHONETIC_DISCOUNT = original_phonetic
        sources_module._WORD_MATCH_DISCOUNT = original_word

    return reports
