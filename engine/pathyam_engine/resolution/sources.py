"""Candidate lookup over the multilingual lexicon.

Two implementations behind one interface. The Postgres one prefers ``pg_trgm``
indexes and **degrades to Python-side scoring when the extension is absent** rather
than failing - managed hosts vary, and a resolver that dies because contrib is missing
is worse than one that runs a little slower and says so.

Because :mod:`.trigram` reimplements pg_trgm's scoring faithfully, the degraded path
returns the same ranking as the indexed path. It is slower, not different.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from .trigram import normalize, phonetic_similarity, similarity, word_similarity

# Containment must never beat a full match: word_similarity("dosa", "masala dosa")
# is 1.0, which would tie plain dosa with masala dosa. A small discount keeps the
# ordering right without suppressing genuine partial matches.
_WORD_MATCH_DISCOUNT = 0.94
# Phonetic matches are real but coarser than a direct trigram hit, so they rank below.
_PHONETIC_DISCOUNT = 0.90

__all__ = ["RawCandidate", "LexiconEntry", "CandidateSource",
           "InMemoryCandidateSource", "PostgresCandidateSource"]


@dataclass(frozen=True)
class LexiconEntry:
    food_id: int
    pathyam_id: str
    name_en: str
    lang: str
    surface: str                 # the native-script or romanised form as stored
    is_primary: bool = False
    region_id: int | None = None
    template_id: int | None = None
    food_group: str | None = None
    # The unmodified member of a dish family. "dosa" is contained equally in
    # "Dosa, plain", "Masala dosa", "Rava dosa" and "Ghee roast dosa" — all four
    # score an identical 0.990 and the tie breaks alphabetically, so a bare query
    # for the head noun returned masala dosa. No string signal can separate them;
    # a user typing the bare noun means the plain version, and that is product
    # knowledge, not something to infer. Needs ref.food_item.is_base_variant to
    # exist before PostgresCandidateSource can populate it.
    is_base: bool = False


@dataclass(frozen=True)
class RawCandidate:
    entry: LexiconEntry
    similarity: float
    method: str                  # 'exact' | 'trigram' | 'vector'


class CandidateSource(abc.ABC):
    @abc.abstractmethod
    def lookup(
        self, phrase: str, *, lang_hint: Sequence[str] = (), limit: int = 20,
        min_similarity: float = 0.20,
    ) -> list[RawCandidate]: ...


def _score_entries(
    phrase: str, entries: Iterable[LexiconEntry], min_similarity: float
) -> list[RawCandidate]:
    """Shared scoring so both sources rank identically."""
    target = normalize(phrase)
    out: list[RawCandidate] = []
    for entry in entries:
        surface_norm = normalize(entry.surface)
        if not surface_norm:
            continue
        if surface_norm == target:
            out.append(RawCandidate(entry, 1.0, "exact"))
            continue
        # Three ways to match, best wins:
        #   direct trigram   - "dosai" vs "dosa"
        #   word containment - "dosa" inside "dosa masala"  (discounted)
        #   phonetic         - "thosai" vs "dosa"           (discounted further)
        direct = similarity(target, surface_norm)
        contained = word_similarity(target, surface_norm) * _WORD_MATCH_DISCOUNT
        phonetic = phonetic_similarity(target, surface_norm) * _PHONETIC_DISCOUNT

        score = max(direct, contained)
        method = "trigram"
        if phonetic > score:
            score, method = phonetic, "phonetic"

        if score >= min_similarity:
            out.append(RawCandidate(entry, score, method))
    return out


class InMemoryCandidateSource(CandidateSource):
    """For tests, offline lexicon evaluation, and small embedded deployments."""

    def __init__(self, entries: Sequence[LexiconEntry]) -> None:
        self.entries = list(entries)

    def lookup(
        self, phrase: str, *, lang_hint: Sequence[str] = (), limit: int = 20,
        min_similarity: float = 0.20,
    ) -> list[RawCandidate]:
        scored = _score_entries(phrase, self.entries, min_similarity)
        scored.sort(key=lambda c: (c.method != "exact", -c.similarity,
                                   not c.entry.is_primary))
        return scored[:limit]


class PostgresCandidateSource(CandidateSource):
    """Reads ``ref.food_name`` joined to ``ref.food_item`` and ``ref.recipe_template``."""

    _SELECT = """
        SELECT fn.food_id, fi.pathyam_id, fi.canonical_name_en, fn.lang,
               coalesce(fn.name_native, fn.name_roman) AS surface,
               fn.name_normalized, fn.is_primary, fn.region_id,
               rt.template_id, fi.food_group
          FROM ref.food_name fn
          JOIN ref.food_item fi ON fi.food_id = fn.food_id
          LEFT JOIN LATERAL (
                SELECT t.template_id FROM ref.recipe_template t
                 WHERE t.food_id = fn.food_id AND t.is_active
                 ORDER BY t.version DESC, t.template_id LIMIT 1) rt ON true
    """

    def __init__(self, conn: Any) -> None:
        self._conn = conn
        self._has_trgm = self._detect_trgm()
        self._cache: list[LexiconEntry] | None = None
        self.warnings: list[str] = []
        if not self._has_trgm:
            self.warnings.append(
                "pg_trgm is not installed; falling back to Python-side trigram "
                "scoring. Ranking is identical, latency is not. Run "
                "db/011_trgm_indexes_optional.sql on a host with contrib."
            )

    def _detect_trgm(self) -> bool:
        with self._conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")
            return cur.fetchone() is not None

    def _row_to_entry(self, r) -> LexiconEntry:
        return LexiconEntry(
            food_id=r[0], pathyam_id=r[1], name_en=r[2], lang=r[3], surface=r[4],
            is_primary=r[6], region_id=r[7], template_id=r[8], food_group=r[9],
        )

    def _all_entries(self) -> list[LexiconEntry]:
        if self._cache is None:
            with self._conn.cursor() as cur:
                cur.execute(self._SELECT)
                self._cache = [self._row_to_entry(r) for r in cur.fetchall()]
        return self._cache

    def lookup(
        self, phrase: str, *, lang_hint: Sequence[str] = (), limit: int = 20,
        min_similarity: float = 0.20,
    ) -> list[RawCandidate]:
        target = normalize(phrase)
        if not target:
            return []

        if not self._has_trgm:
            scored = _score_entries(phrase, self._all_entries(), min_similarity)
            scored.sort(key=lambda c: (-c.similarity, not c.entry.is_primary))
            return scored[:limit]

        with self._conn.cursor() as cur:
            cur.execute("SET LOCAL pg_trgm.similarity_threshold = %s", (min_similarity,))
            cur.execute(
                self._SELECT + """
                 WHERE fn.name_normalized %% %s
                    OR fn.name_normalized = %s
                 ORDER BY greatest(similarity(fn.name_normalized, %s),
                                   word_similarity(%s, fn.name_normalized)) DESC,
                          fn.is_primary DESC
                 LIMIT %s""",
                (target, target, target, target, limit),
            )
            rows = cur.fetchall()

        out: list[RawCandidate] = []
        for r in rows:
            entry = self._row_to_entry(r)
            normalized = r[5]
            if normalized == target:
                out.append(RawCandidate(entry, 1.0, "exact"))
            else:
                out.append(RawCandidate(
                    entry,
                    max(similarity(target, normalized), word_similarity(target, normalized)),
                    "trigram",
                ))
        out.sort(key=lambda c: (-c.similarity, not c.entry.is_primary))
        return out
