"""Dish resolution: free text -> ranked candidate dishes.

    from pathyam_engine.resolution import DishResolver, PostgresCandidateSource

    resolver = DishResolver(PostgresCandidateSource(conn))
    items = resolver.resolve_text("2 masale dose, swalpa enne",
                                  preferred_lang="kn", region_id=3)

This package returns identities and scores. It never returns a nutrient value -
that is the compute engine's job, and keeping the boundary sharp is what makes the
numbers reproducible.
"""

from .resolver import Candidate, DishResolver, ResolvedItem
from .sources import (
    CandidateSource, InMemoryCandidateSource, LexiconEntry, PostgresCandidateSource,
    RawCandidate,
)
from .text_parser import MODIFIERS, NUMBER_WORDS, UNIT_WORDS, ParsedItem, parse_log
from .trigram import normalize, similarity, trigrams, word_similarity

__all__ = [
    "DishResolver", "Candidate", "ResolvedItem",
    "CandidateSource", "InMemoryCandidateSource", "PostgresCandidateSource",
    "LexiconEntry", "RawCandidate",
    "parse_log", "ParsedItem", "NUMBER_WORDS", "UNIT_WORDS", "MODIFIERS",
    "similarity", "word_similarity", "trigrams", "normalize",
]
