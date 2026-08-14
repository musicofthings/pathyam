"""Authoring pipeline: YAML template library -> validation -> Postgres -> audit.

    python -m pathyam_engine.authoring validate
    python -m pathyam_engine.authoring load --dsn ...
    python -m pathyam_engine.authoring audit --dsn ...

Authoring ~400 templates is the critical path for Phase 1, and the bottleneck is
review capacity, not typing. So the validator is the deliverable: it has to catch the
mistakes a reviewer would miss, particularly the silent ones.
"""

from .audit import AuditReport, IngredientGap, TemplateStatus, audit
from .lexicon import (
    DishEntry, LexiconLoadResult, load_lexicon_file, load_lexicon_into_postgres,
)
from .loader import LoadResult, load_into_postgres
from .schema import (
    Ingredient, IngredientRef, ParamSpec, Template, TemplateLibrary,
    ValidationIssue, load_library,
)

__all__ = [
    "load_library", "TemplateLibrary", "Template", "ParamSpec", "IngredientRef",
    "Ingredient", "ValidationIssue",
    "load_into_postgres", "LoadResult",
    "load_lexicon_file", "load_lexicon_into_postgres", "DishEntry", "LexiconLoadResult",
    "audit", "AuditReport", "TemplateStatus", "IngredientGap",
]
