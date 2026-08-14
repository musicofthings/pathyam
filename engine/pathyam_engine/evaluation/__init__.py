"""Evaluation harness for dish resolution.

    python -m pathyam_engine.evaluation                 # full report + ablations
    python -m pathyam_engine.evaluation --json          # machine-readable
    python -m pathyam_engine.evaluation --failures 40   # show more failures

Build this before adding a retrieval component, not after: an ablation is the only
honest way to decide whether the next model earns its cost.
"""

from .harness import (
    EvalReport, GoldenQuery, QueryOutcome, ablate, build_source, evaluate,
    load_lexicon, load_queries,
)

__all__ = [
    "EvalReport", "GoldenQuery", "QueryOutcome",
    "ablate", "build_source", "evaluate", "load_lexicon", "load_queries",
]
