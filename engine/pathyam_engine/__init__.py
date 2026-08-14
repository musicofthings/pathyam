"""Pathyam compute engine.

Deterministic nutrient computation over parametric recipe templates, with
uncertainty propagated by Monte Carlo and provenance carried on every value.

    from pathyam_engine import ComputeEngine, PostgresRepository
    import psycopg

    with psycopg.connect(DSN) as conn:
        engine = ComputeEngine(PostgresRepository(conn))
        result = engine.compute("PY-T-000101", region_key="KA",
                                param_overrides={"fat_g": 11.0, "fat_type": "ghee"})
        print(result.summary_line())

No part of this package calls a language model. Perception and entity resolution
happen upstream and hand this engine a template plus parameter estimates; the
engine's contract is that identical inputs produce byte-identical output.
"""

from .distributions import Prior, PriorError, sample_prior
from .engine import ENGINE_VERSION, ComputeEngine, EngineError
from .expressions import ExpressionError, evaluate, validate
from .models import (
    ComputeResult, CompositionValue, Nutrient, NutrientResult, Percentiles,
    RecipeTemplate, RetentionFactor, SourceRef, TemplateIngredient, TemplateParameter,
)
from .qc import QCResult, run_all as run_qc
from .repository import InMemoryRepository, PostgresRepository, Repository

__version__ = ENGINE_VERSION

__all__ = [
    "ComputeEngine", "EngineError", "ENGINE_VERSION",
    "Repository", "PostgresRepository", "InMemoryRepository",
    "Prior", "PriorError", "sample_prior",
    "evaluate", "validate", "ExpressionError",
    "ComputeResult", "NutrientResult", "Percentiles", "RecipeTemplate",
    "TemplateParameter", "TemplateIngredient", "CompositionValue", "Nutrient",
    "RetentionFactor", "SourceRef",
    "QCResult", "run_qc",
    "__version__",
]
