"""Trigram similarity, deliberately bug-compatible with PostgreSQL's ``pg_trgm``.

Why reimplement something the database already does: the resolver has to run in three
places that do not all have Postgres in front of them - unit tests, offline lexicon
evaluation, and any future on-device path. Reimplementing the *same* scoring function
means a candidate ranked 3rd offline is ranked 3rd in production, so an eval harness
built on this module says something true about the deployed system.

``pg_trgm`` splits on non-alphanumerics, prefixes each word with two spaces, suffixes
one, and takes 3-grams. ``similarity()`` is then Jaccard over those sets::

    show_trgm('dosa') -> {"  d", " do", "dos", "osa", "sa "}

The padding is not cosmetic: it weights word beginnings, which is what makes
"dosai"/"dosa" score well while "masala dosa"/"dosa" scores lower.

Unicode note: ``pg_trgm`` is byte-oriented for multibyte text unless built with
``--enable-ustrings``, so scores for native Tamil/Telugu/Malayalam/Kannada script will
not match the database exactly. Romanised forms - which is what users type most - match
faithfully. Native-script lookups should go through exact match on ``name_normalized``,
which they do in :mod:`.resolver`.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

__all__ = ["trigrams", "similarity", "word_similarity", "normalize",
           "phonetic_key", "phonetic_similarity"]

# Unicode general categories to KEEP: letters (L*), marks (M*), numbers (N*).
#
# This must be a keep-list, not a strip-list. The obvious implementation -
# re.sub(r'[^\w]+', ' ', text) - silently destroys every Indic script this product
# exists for, because Python's \w excludes combining marks (category Mn/Mc) and Tamil
# vowel signs are marks:
#
#     'தோசை'  ->  'த ச'      (both vowel signs deleted)
#
# PostgreSQL's [[:alnum:]] DOES retain those marks, so ref.normalize_name() and this
# function would have disagreed on precisely the four languages that matter, and the
# native-script half of the lexicon would have quietly stopped matching.
_KEEP_CATEGORIES = ("L", "M", "N")


def normalize(text: str) -> str:
    """Mirror of ``ref.normalize_name()`` in the database.

    Lowercase, strip punctuation and symbols, collapse whitespace, preserve letters,
    combining marks and digits. Kept in lockstep with the SQL function - a divergence
    here does not raise, it just degrades matching, which is far harder to notice.
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text.lower())
    kept = [
        ch if unicodedata.category(ch)[0] in _KEEP_CATEGORIES else " "
        for ch in text
    ]
    return " ".join("".join(kept).split())


@lru_cache(maxsize=8192)
def trigrams(text: str) -> frozenset[str]:
    """Trigram set using pg_trgm's padding rules."""
    normalized = normalize(text)
    if not normalized:
        return frozenset()
    out: set[str] = set()
    for word in normalized.split():
        padded = f"  {word} "
        for i in range(len(padded) - 2):
            out.add(padded[i:i + 3])
    return frozenset(out)


def similarity(a: str, b: str) -> float:
    """Jaccard similarity over trigram sets, as ``pg_trgm.similarity()`` computes it."""
    ta, tb = trigrams(a), trigrams(b)
    if not ta or not tb:
        return 0.0
    intersection = len(ta & tb)
    if intersection == 0:
        return 0.0
    return intersection / (len(ta) + len(tb) - intersection)


# Romanisation folding for Indic text. South Indian transliteration is wildly
# inconsistent - the same dish is written dosa / dosai / dhosa / thosai / dose / dosey
# depending on who is typing - and trigram similarity does not bridge those:
# similarity('dosa', 'thosai') is 0.09, well below any usable retrieval threshold.
#
# Folding to a coarse phonetic key does bridge them. Order matters: digraphs must be
# collapsed before vowel folding, or 'th' becomes 't' + a stray vowel rule.
_PHONETIC_RULES: tuple[tuple[str, str], ...] = (
    ("zh", "l"),                                    # tamizh -> tamil
    ("th", "t"), ("dh", "d"), ("bh", "b"), ("gh", "g"), ("kh", "k"),
    ("ph", "f"), ("ch", "c"), ("sh", "s"), ("jh", "j"),
    ("aa", "a"), ("ee", "i"), ("ii", "i"), ("oo", "u"), ("uu", "u"),
    ("ai", "e"), ("ay", "e"), ("au", "o"), ("ow", "o"),
    ("w", "v"), ("y", "i"), ("q", "k"), ("x", "ks"),
)

# Voiced/unvoiced folding. Indic stops are allophonic - Tamil த is written th, t, d or
# dh by different people for the same sound - so a phonetic key that distinguishes them
# still fails to match 'thosai' to 'dosa'. Folding each pair to one representative is
# what actually bridges the transliterations users type.
#
# It is deliberately aggressive, and safe because phonetic matches are discounted and
# rank below direct trigram hits: a true match always outranks a phonetic near-miss.
_STOP_FOLDING = str.maketrans({"t": "d", "g": "k", "b": "p", "j": "c"})

_VOWELS = set("aeiou")


@lru_cache(maxsize=8192)
def phonetic_key(text: str) -> str:
    """Coarse phonetic key for romanised Indic words.

    Collapses common transliteration variants, de-duplicates repeated letters, and
    drops trailing vowels (which carry the dosa/dosai/dose variation almost entirely).
    Applied only to Latin script - native script is already unambiguous.
    """
    normalized = normalize(text)
    if not normalized or not normalized.isascii():
        return normalized

    words = []
    for word in normalized.split():
        for src, dst in _PHONETIC_RULES:
            word = word.replace(src, dst)
        word = word.translate(_STOP_FOLDING)
        # collapse doubled consonants: idlli -> idli, vadda -> vada
        collapsed = []
        for ch in word:
            if not collapsed or collapsed[-1] != ch:
                collapsed.append(ch)
        word = "".join(collapsed)
        # drop a trailing vowel: dosa/dose/dosi all key to 'dos'
        while len(word) > 2 and word[-1] in _VOWELS:
            word = word[:-1]
        if word:
            words.append(word)
    return " ".join(words)


def phonetic_similarity(a: str, b: str) -> float:
    """Trigram similarity over phonetic keys. 0.0 when either side is non-Latin."""
    ka, kb = phonetic_key(a), phonetic_key(b)
    if not ka or not kb or not ka.isascii() or not kb.isascii():
        return 0.0
    return similarity(ka, kb)


def word_similarity(needle: str, haystack: str) -> float:
    """Best similarity between ``needle`` and any contiguous word run in ``haystack``.

    Approximates ``pg_trgm.word_similarity()``. This is what lets "dosa" score highly
    against "Dosa, masala" - plain :func:`similarity` punishes the extra token, but a
    user typing one word of a two-word dish still means that dish.
    """
    words = normalize(haystack).split()
    if not words:
        return 0.0
    needle_words = len(normalize(needle).split()) or 1
    best = 0.0
    # Windows around the needle's own length; wider windows only dilute the score.
    for size in range(1, min(len(words), needle_words + 1) + 1):
        for start in range(len(words) - size + 1):
            best = max(best, similarity(needle, " ".join(words[start:start + size])))
    return best
