#!/usr/bin/env python3
"""Split romanised golden queries into what the resolver can and cannot derive.

    PYTHONPATH=. python3 eval/lexeme_ceiling.py

The held-out evaluation removes a query's own alias from the lexicon and asks the
resolver to find the dish anyway. That is the right test for a SPELLING VARIANT --
"dosai" should reach dosa_plain through "dosa". It is not a meaningful test for a
DISTINCT REGIONAL LEXEME: "huli" is the Kannada word for sambar and shares no
morphology with "sambar", so no phonetic or trigram method can bridge it. Removing
that alias and scoring the miss measures lexicon coverage, not resolver quality.

Both live in the same held-out number, which makes the ceiling look further away
than it is. This script separates them so nobody targets an impossible figure.
"""

from __future__ import annotations

import difflib
import re
import sys
from pathlib import Path

import yaml

EVAL_DIR = Path(__file__).resolve().parent
# Below this similarity to every sibling form, treat the query as a distinct lexeme.
THRESHOLD = 0.55


def _norm(s: str) -> str:
    return re.sub(r"[^a-z]", "", (s or "").lower())


def main(category: str = "romanised") -> int:
    dishes = {d["key"]: d for d in yaml.safe_load(
        (EVAL_DIR / "lexicon_south_indian.yaml").read_text())["dishes"]}
    queries = yaml.safe_load((EVAL_DIR / "golden_queries.yaml").read_text())["queries"]

    derivable, distinct = [], []
    for q in queries:
        if q.get("category") != category:
            continue
        key = q.get("expect")
        dish = dishes.get(key) if key else None
        if dish is None:
            continue

        siblings = [dish["en"]] + [
            a for a in dish.get("aliases", []) if _norm(a) != _norm(q["q"])
        ]
        score, closest = max(
            ((difflib.SequenceMatcher(None, _norm(q["q"]), _norm(s)).ratio(), s)
             for s in siblings),
            default=(0.0, "-"),
        )
        (derivable if score >= THRESHOLD else distinct).append(
            (q["q"], key, score, closest))

    total = len(derivable) + len(distinct)
    if not total:
        print(f"no '{category}' queries found")
        return 1

    print(f"{category}: {total} golden queries\n")
    print(f"  SPELLING VARIANTS  {len(derivable):>3}  "
          f"a sibling form is >= {THRESHOLD} similar; the resolver should reach these")
    print(f"  DISTINCT LEXEMES   {len(distinct):>3}  "
          f"no sibling is close; only lexicon coverage can supply these\n")
    for q, key, score, closest in sorted(distinct, key=lambda r: r[2]):
        print(f"    {q:<22} -> {key:<18} nearest {score:.2f}  '{closest}'")

    ceiling = len(derivable) / total * 100.0
    print(f"\n  Held-out ceiling for {category}: {ceiling:.1f}%")
    print("  A perfect resolver cannot exceed this while those lexemes are held out.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "romanised"))
