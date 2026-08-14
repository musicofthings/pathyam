"""Dish resolution: free text in, ranked candidates out.

The contract that matters: **this module returns identities and scores, never
nutrient values.** Retrieval decides *what* was eaten; the compute engine decides
*what is in it*. Collapsing the two is how a nutrition app ends up with numbers it
cannot reproduce or defend.

Reranking is intentionally a small, inspectable set of additive boosts rather than a
learned model. At launch there is no interaction data to train on, and a transparent
rule you can explain to a dietitian beats an opaque one you cannot. Once
``ml.user_correction`` has volume, swap :meth:`DishResolver._rerank` for a trained
cross-encoder - the interface is built for that substitution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from .sources import CandidateSource, RawCandidate
from .text_parser import ParsedItem, parse_log

__all__ = ["Candidate", "ResolvedItem", "DishResolver"]

# Reranking weights. Small relative to base similarity so they break ties rather
# than overturn genuine matches.
_BOOST_PRIMARY = 0.05          # the canonical name for a language
_BOOST_LANG_MATCH = 0.08       # matched in a language the user actually writes
_BOOST_SCRIPT_MATCH = 0.06     # matched in the script they typed in
_BOOST_REGION = 0.05           # regional name variant for the user's region
_BOOST_HAS_TEMPLATE = 0.04     # computable dish beats a bare ingredient
_BOOST_BASE_VARIANT = 0.03     # bare head noun means the unmodified dish

# Below these, ask rather than assume.
_CONFIDENCE_FLOOR = 0.72
_MARGIN_FLOOR = 0.08

# Retrieval floor. Swept against the golden set (see eval/ and RESOLUTION_EVAL.md):
#
#   min_sim   top-1    top-5   absent-noise
#     0.20    83.3%    93.5%      42.9%
#     0.35    83.3%    92.0%      28.6%      <- chosen
#     0.40    81.9%    89.1%      28.6%
#
# 0.35 is free on top-1 and cuts by a third the rate at which a dish we do not stock
# still gets offered a candidate — a user typing "pizza" being asked "did you mean
# puttu?" is a worse failure than losing 1.5 points of top-5 recall, because the
# confirmation gate withholds the low-confidence matches anyway. Revisit once the
# alias table is built out, since that shifts the recall side of the trade.
_MIN_SIMILARITY = 0.35


@dataclass
class Candidate:
    food_id: int
    pathyam_id: str
    name_en: str
    matched_text: str
    lang: str
    score: float
    base_similarity: float
    method: str
    template_id: int | None = None
    food_group: str | None = None
    boosts: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "food_id": self.food_id, "pathyam_id": self.pathyam_id,
            "name_en": self.name_en, "matched_text": self.matched_text,
            "lang": self.lang, "score": round(self.score, 4),
            "base_similarity": round(self.base_similarity, 4),
            "method": self.method, "template_id": self.template_id,
            "food_group": self.food_group,
            "boosts": {k: round(v, 3) for k, v in self.boosts.items()},
        }


@dataclass
class ResolvedItem:
    parsed: ParsedItem
    candidates: list[Candidate]

    @property
    def best(self) -> Candidate | None:
        return self.candidates[0] if self.candidates else None

    @property
    def confidence(self) -> float:
        return self.candidates[0].score if self.candidates else 0.0

    @property
    def margin(self) -> float:
        """Gap to the runner-up. A small margin means genuinely ambiguous, not weak."""
        if len(self.candidates) < 2:
            return self.confidence
        return self.candidates[0].score - self.candidates[1].score

    @property
    def needs_confirmation(self) -> bool:
        if not self.candidates:
            return True
        # A single exact match is not ambiguous, whatever the margin says.
        # Typing the exact Tamil name for plain dosa produced margin 0.00 - because
        # masala dosa contains that word and its boosts pushed it to the same score -
        # so the app asked the user to confirm something they had spelled perfectly.
        exact = [c for c in self.candidates if c.method == "exact"]
        if len(exact) == 1 and exact[0] is self.candidates[0]:
            return False
        return self.confidence < _CONFIDENCE_FLOOR or self.margin < _MARGIN_FLOOR

    def as_dict(self) -> dict[str, Any]:
        return {
            "parsed": self.parsed.as_dict(),
            "candidates": [c.as_dict() for c in self.candidates],
            "confidence": round(self.confidence, 4),
            "margin": round(self.margin, 4),
            "needs_confirmation": self.needs_confirmation,
        }


class DishResolver:
    def __init__(self, source: CandidateSource) -> None:
        self.source = source

    def resolve_text(
        self,
        text: str,
        *,
        region_id: int | None = None,
        preferred_lang: str | None = None,
        limit: int = 5,
        min_similarity: float = _MIN_SIMILARITY,
    ) -> list[ResolvedItem]:
        """Parse a log line and resolve each item it contains."""
        return [
            self.resolve_item(item, region_id=region_id, preferred_lang=preferred_lang,
                              limit=limit, min_similarity=min_similarity)
            for item in parse_log(text)
        ]

    def resolve_item(
        self,
        item: ParsedItem,
        *,
        region_id: int | None = None,
        preferred_lang: str | None = None,
        limit: int = 5,
        min_similarity: float = _MIN_SIMILARITY,
    ) -> ResolvedItem:
        if not item.dish_phrase:
            return ResolvedItem(parsed=item, candidates=[])

        raw = self.source.lookup(
            item.dish_phrase, lang_hint=item.scripts,
            limit=max(limit * 6, 30), min_similarity=min_similarity,
        )
        candidates = self._rerank(
            raw, item=item, region_id=region_id, preferred_lang=preferred_lang
        )
        return ResolvedItem(parsed=item, candidates=candidates[:limit])

    # ------------------------------------------------------------- reranking --

    def _rerank(
        self,
        raw: Sequence[RawCandidate],
        *,
        item: ParsedItem,
        region_id: int | None,
        preferred_lang: str | None,
    ) -> list[Candidate]:
        scripts = set(item.scripts)
        best_per_food: dict[int, Candidate] = {}

        for rc in raw:
            entry = rc.entry
            boosts: dict[str, float] = {}

            if entry.is_primary:
                boosts["primary"] = _BOOST_PRIMARY
            if preferred_lang and entry.lang == preferred_lang:
                boosts["preferred_lang"] = _BOOST_LANG_MATCH
            # Typing in Tamil script is strong evidence the Tamil name is the match.
            if entry.lang in scripts and entry.lang != "en":
                boosts["script_match"] = _BOOST_SCRIPT_MATCH
            if region_id is not None and entry.region_id == region_id:
                boosts["region"] = _BOOST_REGION
            if entry.template_id is not None:
                boosts["computable"] = _BOOST_HAS_TEMPLATE
            # Only when the query did not itself name a variant: "masala dosa" must
            # not be dragged toward plain dosa just because plain is the base.
            if entry.is_base and len(item.dish_phrase.split()) <= 1:
                boosts["base_variant"] = _BOOST_BASE_VARIANT

            # Boosts consume a fraction of the REMAINING headroom to the ceiling
            # rather than being added and clamped.
            #
            # A hard clamp silently destroys the signal: four dosa variants all scored
            # base 0.940, and with boosts of 0.09-0.12 every one of them clamped to
            # exactly 0.990. Adding a base-variant boost changed nothing at all,
            # because the ceiling had already flattened the ranking. Saturation is the
            # worst kind of scoring bug — it looks like the feature does not help.
            #
            # Fractional headroom is monotonic in both similarity and boost, so a
            # fuzzy match still can never reach a true exact match's score.
            ceiling = 1.0 if rc.method == "exact" else 0.99
            base = min(rc.similarity, ceiling)
            headroom = ceiling - base
            score = base + min(1.0, sum(boosts.values())) * headroom
            candidate = Candidate(
                food_id=entry.food_id, pathyam_id=entry.pathyam_id,
                name_en=entry.name_en, matched_text=entry.surface, lang=entry.lang,
                score=score, base_similarity=rc.similarity, method=rc.method,
                template_id=entry.template_id, food_group=entry.food_group,
                boosts=boosts,
            )

            # One row per food: a dish matching in four languages is one candidate,
            # not four. Keep whichever language matched best.
            existing = best_per_food.get(entry.food_id)
            if existing is None or candidate.score > existing.score:
                best_per_food[entry.food_id] = candidate

        # Exact matches sort first regardless of boosts. Otherwise typing the exact
        # Tamil name for plain dosa can lose to masala dosa, which contains it as a
        # word and picks up an extra boost or two.
        return sorted(
            best_per_food.values(),
            key=lambda c: (c.method != "exact", -c.score, c.name_en),
        )
