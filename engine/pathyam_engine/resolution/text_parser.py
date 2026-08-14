"""Parsing free-text meal logs in Tamil, Telugu, Malayalam, Kannada and English.

Real logs are code-mixed and rarely well-formed::

    "2 masala dosa"
    "rendu idli sambar"
    "ஒரு தோசை"
    "3 idli + sambar konjam"
    "oru plate masale dose, swalpa enne"
    "2 katori sambar and 1 dosa"

The parser's job is to split that into items and pull out quantity, unit, dish phrase
and any magnitude modifiers. It does NOT identify the dish - that is the resolver's
job, and keeping them separate means the parser can be tested exhaustively against
strings without needing a lexicon.

Modifiers are not decoration. "konjam ennai" (Tamil: little oil) and "swalpa enne"
(Kannada: little oil) are the user volunteering a value for the ``fat_g`` parameter.
Capturing them means the app does not have to ask a question it was already told the
answer to.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

__all__ = ["ParsedItem", "parse_log", "NUMBER_WORDS", "UNIT_WORDS", "MODIFIERS"]


# Numerals 1-10 across the four languages, in native script and the romanisations
# people actually type - including colloquial forms ("rendu" not just "irandu",
# "moonu" not just "moondru"), which is what appears in real logs.
NUMBER_WORDS: dict[str, float] = {
    # English
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "half": 0.5, "quarter": 0.25, "couple": 2,
    # Tamil - native
    "ஒன்று": 1, "ஒரு": 1, "இரண்டு": 2, "ரெண்டு": 2, "மூன்று": 3, "மூணு": 3,
    "நான்கு": 4, "நாலு": 4, "ஐந்து": 5, "அஞ்சு": 5, "ஆறு": 6, "ஏழு": 7,
    "எட்டு": 8, "ஒன்பது": 9, "பத்து": 10, "அரை": 0.5,
    # Tamil - romanised
    "oru": 1, "onnu": 1, "ondru": 1, "irandu": 2, "rendu": 2, "moondru": 3,
    "moonu": 3, "naalu": 4, "naangu": 4, "anju": 5, "aindhu": 5, "aaru": 6,
    "ezhu": 7, "ettu": 8, "onbadhu": 9, "pathu": 10, "arai": 0.5,
    # Telugu - native
    "ఒకటి": 1, "ఒక": 1, "రెండు": 2, "మూడు": 3, "నాలుగు": 4, "అయిదు": 5,
    "ఐదు": 5, "ఆరు": 6, "ఏడు": 7, "ఎనిమిది": 8, "తొమ్మిది": 9, "పది": 10,
    # Telugu - romanised
    "oka": 1, "okati": 1, "moodu": 3, "naalugu": 4, "aidu": 5, "aaru_te": 6,
    # Malayalam - native
    "ഒന്ന്": 1, "ഒരു": 1, "രണ്ട്": 2, "മൂന്ന്": 3, "നാല്": 4, "അഞ്ച്": 5,
    "ആറ്": 6, "ഏഴ്": 7, "എട്ട്": 8, "ഒമ്പത്": 9, "പത്ത്": 10, "പകുതി": 0.5,
    # Malayalam - romanised
    "randu": 2, "moonnu": 3, "anchu": 5,
    # Kannada - native
    "ಒಂದು": 1, "ಎರಡು": 2, "ಮೂರು": 3, "ನಾಲ್ಕು": 4, "ಐದು": 5, "ಆರು": 6,
    "ಏಳು": 7, "ಎಂಟು": 8, "ಒಂಬತ್ತು": 9, "ಹತ್ತು": 10, "ಅರ್ಧ": 0.5,
    # Kannada - romanised
    "ondu": 1, "eradu": 2, "mooru": 3, "naalku": 4,
}

# Serving-unit words -> unit_key in ref.serving_unit. Unknown units fall through as
# part of the dish phrase rather than being dropped.
UNIT_WORDS: dict[str, str] = {
    "katori": "katori_south", "katoris": "katori_south",
    "bowl": "katori_south", "bowls": "katori_south",
    "கிண்ணம்": "katori_south", "ಬಟ್ಟಲು": "katori_south",
    "കിണ്ണം": "katori_south", "గిన్నె": "katori_south",
    "cup": "cup", "cups": "cup",
    "glass": "glass", "glasses": "glass",
    "tumbler": "tumbler", "டம்ளர்": "tumbler",
    "plate": "plate", "plates": "plate",
    "piece": "piece", "pieces": "piece", "pcs": "piece", "nos": "piece",
    "spoon": "spoon", "tsp": "tsp", "tbsp": "tbsp",
    "ladle": "ladle", "scoop": "ladle",
    "g": "gram", "gm": "gram", "gms": "gram", "gram": "gram", "grams": "gram",
    "ml": "millilitre", "l": "litre",
}

# Magnitude modifiers. `scale` multiplies a quantity or parameter; `target` names the
# parameter when the modifier attaches to a known ingredient word.
MODIFIERS: dict[str, dict[str, Any]] = {
    # "a little"
    "konjam": {"scale": 0.6, "lang": "ta"}, "konjum": {"scale": 0.6, "lang": "ta"},
    "கொஞ்சம்": {"scale": 0.6, "lang": "ta"},
    "swalpa": {"scale": 0.6, "lang": "kn"}, "ಸ್ವಲ್ಪ": {"scale": 0.6, "lang": "kn"},
    "korachu": {"scale": 0.6, "lang": "ml"}, "കുറച്ച്": {"scale": 0.6, "lang": "ml"},
    "koncham": {"scale": 0.6, "lang": "te"}, "కొంచెం": {"scale": 0.6, "lang": "te"},
    "little": {"scale": 0.6, "lang": "en"}, "less": {"scale": 0.6, "lang": "en"},
    "kammi": {"scale": 0.6, "lang": "te"}, "kam": {"scale": 0.6, "lang": "en"},
    # "a lot"
    "romba": {"scale": 1.5, "lang": "ta"}, "நிறைய": {"scale": 1.5, "lang": "ta"},
    "nirya": {"scale": 1.5, "lang": "ta"}, "jaasti": {"scale": 1.5, "lang": "kn"},
    "jasti": {"scale": 1.5, "lang": "kn"}, "ಜಾಸ್ತಿ": {"scale": 1.5, "lang": "kn"},
    "ekkuva": {"scale": 1.5, "lang": "te"}, "valare": {"scale": 1.5, "lang": "ml"},
    "extra": {"scale": 1.5, "lang": "en"}, "lots": {"scale": 1.5, "lang": "en"},
    "more": {"scale": 1.5, "lang": "en"}, "heavy": {"scale": 1.5, "lang": "en"},
}

# Ingredient words a modifier can attach to, mapped to the engine parameter they hint.
_PARAM_WORDS: dict[str, str] = {
    "oil": "fat_g", "ennai": "fat_g", "எண்ணெய்": "fat_g", "enne": "fat_g",
    "ಎಣ್ಣೆ": "fat_g", "എണ്ണ": "fat_g", "nune": "fat_g", "నూనె": "fat_g",
    "ghee": "fat_g", "நெய்": "fat_g", "ney": "fat_g", "neyyi": "fat_g",
    "sugar": "sugar_g", "chakkara": "sugar_g", "sakkarai": "sugar_g",
    "salt": "salt_g", "uppu": "salt_g",
}

_SPLIT = re.compile(r"\s*(?:\+|,|;|&|\band\b|\bmatthu\b|\bmariyu\b|\bmattu\b)\s*", re.IGNORECASE)
# The fraction alternative MUST come first: with the decimal branch leading, "1/2"
# matches "1" and leaves "/2 dosa" as the dish phrase.
_QTY_PREFIX = re.compile(r"^\s*(\d+\s*/\s*\d+|\d+(?:\.\d+)?)\s*(.*)$", re.DOTALL)
_FRACTION = re.compile(r"^(\d+)\s*/\s*(\d+)$")


@dataclass
class ParsedItem:
    """One item extracted from a log line. Carries no nutrition and no dish identity."""

    raw_text: str
    dish_phrase: str
    quantity: float | None = None
    unit_key: str | None = None
    modifiers: list[str] = field(default_factory=list)
    parameter_hints: dict[str, float] = field(default_factory=dict)
    quantity_scale: float = 1.0
    scripts: list[str] = field(default_factory=list)

    @property
    def effective_quantity(self) -> float:
        """Quantity after modifier scaling. Defaults to 1 when the user gave no number."""
        return (self.quantity if self.quantity is not None else 1.0) * self.quantity_scale

    def as_dict(self) -> dict[str, Any]:
        return {
            "raw_text": self.raw_text, "dish_phrase": self.dish_phrase,
            "quantity": self.quantity, "unit_key": self.unit_key,
            "modifiers": self.modifiers, "parameter_hints": self.parameter_hints,
            "quantity_scale": self.quantity_scale,
            "effective_quantity": self.effective_quantity, "scripts": self.scripts,
        }


def _detect_scripts(text: str) -> list[str]:
    """Which writing systems appear. Used to bias language selection in the resolver."""
    found: set[str] = set()
    for ch in text:
        if not ch.isalpha():
            continue
        try:
            name = unicodedata.name(ch)
        except ValueError:
            continue
        for script, code in (("TAMIL", "ta"), ("TELUGU", "te"),
                             ("MALAYALAM", "ml"), ("KANNADA", "kn"), ("LATIN", "en")):
            if name.startswith(script):
                found.add(code)
                break
    return sorted(found)


def _parse_quantity(token: str) -> float | None:
    m = _FRACTION.match(token.replace(" ", ""))
    if m:
        denominator = float(m.group(2))
        return float(m.group(1)) / denominator if denominator else None
    try:
        return float(token)
    except ValueError:
        return None


def parse_log(text: str) -> list[ParsedItem]:
    """Split a free-text log into items with quantity, unit, dish phrase and modifiers."""
    if not text or not text.strip():
        return []

    items: list[ParsedItem] = []
    for chunk in _SPLIT.split(text):
        chunk = chunk.strip()
        if not chunk:
            continue
        item = _parse_chunk(chunk)

        # A chunk carrying only a modifier belongs to the dish before it.
        # "2 masale dose, swalpa enne" splits on the comma, and treating the tail as
        # a separate item strands the fat_g hint on a dish-less entry where it is
        # silently discarded - the user told us about the oil and we ignored them.
        if not item.dish_phrase and items and (item.parameter_hints or item.modifiers):
            previous = items[-1]
            for name, scale in item.parameter_hints.items():
                previous.parameter_hints.setdefault(name, scale)
            previous.modifiers.extend(item.modifiers)
            if not item.parameter_hints and item.quantity_scale != 1.0:
                previous.quantity_scale *= item.quantity_scale
            previous.raw_text = f"{previous.raw_text}, {item.raw_text}"
            continue

        if item.dish_phrase or item.parameter_hints:
            items.append(item)
    return items


def _parse_chunk(chunk: str) -> ParsedItem:
    item = ParsedItem(raw_text=chunk, dish_phrase="", scripts=_detect_scripts(chunk))

    remainder = chunk
    m = _QTY_PREFIX.match(chunk)
    if m:
        parsed = _parse_quantity(m.group(1))
        if parsed is not None:
            item.quantity = parsed
            remainder = m.group(2)

    tokens = remainder.split()
    dish_tokens: list[str] = []
    pending_modifier: dict[str, Any] | None = None

    for token in tokens:
        bare = token.strip(".,!?;:").lower()
        original = token.strip(".,!?;:")

        # number words, but only before a quantity has been established
        if item.quantity is None and (bare in NUMBER_WORDS or original in NUMBER_WORDS):
            item.quantity = NUMBER_WORDS.get(bare, NUMBER_WORDS.get(original))
            continue

        if bare in UNIT_WORDS or original in UNIT_WORDS:
            item.unit_key = UNIT_WORDS.get(bare, UNIT_WORDS.get(original))
            continue

        modifier = MODIFIERS.get(bare) or MODIFIERS.get(original)
        if modifier is not None:
            item.modifiers.append(bare if bare in MODIFIERS else original)
            pending_modifier = modifier
            continue

        # A modifier immediately before an ingredient word is a parameter hint
        # ("konjam ennai" -> less oil), not a comment on the portion size.
        param = _PARAM_WORDS.get(bare) or _PARAM_WORDS.get(original)
        if param is not None:
            if pending_modifier is not None:
                item.parameter_hints[param] = float(pending_modifier["scale"])
                pending_modifier = None
                continue
            # Bare ingredient word with no modifier: keep it in the dish phrase,
            # because "ghee roast" is a dish, not a hint about fat.
        dish_tokens.append(original)
        pending_modifier = None

    # A trailing modifier with nothing to attach to scales the portion instead.
    if pending_modifier is not None:
        item.quantity_scale = float(pending_modifier["scale"])
    elif item.modifiers and not item.parameter_hints:
        item.quantity_scale = float(MODIFIERS[item.modifiers[0]]["scale"])

    item.dish_phrase = " ".join(dish_tokens).strip()
    return item
