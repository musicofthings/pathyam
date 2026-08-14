"""Dataclasses mirroring the ``ref`` schema, plus the engine's result types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import numpy as np

__all__ = [
    "Nutrient", "CompositionValue", "TemplateParameter", "TemplateIngredient",
    "RecipeTemplate", "RetentionFactor", "SourceRef", "Percentiles",
    "NutrientResult", "IngredientDraw", "ComputeResult",
]

# Confidence tiers, worst-first ordering. Tier A is a directly analysed value;
# tier D is imputed. Clinical outputs default to A and B only.
TIER_ORDER = {"A": 0, "B": 1, "C": 2, "D": 3}


@dataclass(frozen=True)
class Nutrient:
    nutrient_id: int
    tagname: str          # INFOODS tagname, e.g. 'ENERC_KCAL'
    name: str
    unit: str
    decimals: int = 2
    group: str = "other"
    is_core: bool = False


@dataclass(frozen=True)
class CompositionValue:
    food_id: int
    nutrient_id: int
    value: float          # per 100 g unless basis says otherwise
    sd: float | None = None
    confidence: str = "A"
    source_key: str = ""
    basis: str = "per_100g"
    is_borrowed: bool = False


@dataclass(frozen=True)
class TemplateParameter:
    param_name: str
    dtype: str
    prior_dist: str
    prior_params: Mapping[str, Any]
    unit: str | None = None
    observable_from_image: bool = False
    elicitation_question: str | None = None


@dataclass(frozen=True)
class TemplateIngredient:
    qty_expr: str
    food_id: int | None = None
    sub_template_id: int | None = None
    unit: str = "g"
    preparation_state: str | None = None
    cooking_method: str | None = None
    is_optional: bool = False

    def __post_init__(self) -> None:
        if (self.food_id is None) == (self.sub_template_id is None):
            raise ValueError(
                "a template ingredient must reference exactly one of "
                "food_id or sub_template_id"
            )


@dataclass(frozen=True)
class RecipeTemplate:
    template_id: int
    pathyam_id: str
    food_id: int
    base_method: str
    parameters: Sequence[TemplateParameter]
    ingredients: Sequence[TemplateIngredient]
    default_servings: float = 1.0
    yield_factor: float | None = None
    notes: str | None = None

    @property
    def parameter_names(self) -> set[str]:
        return {p.param_name for p in self.parameters}


@dataclass(frozen=True)
class RetentionFactor:
    food_group: str
    cooking_method: str
    nutrient_id: int
    pct_retained: float
    source_key: str = ""


@dataclass(frozen=True)
class SourceRef:
    source_key: str
    citation: str
    licence: str
    is_commercial_cleared: bool


@dataclass(frozen=True)
class Percentiles:
    """A sampled quantity summarised. ``p10``/``p90`` bound an 80% credible interval."""

    p10: float
    p50: float
    p90: float
    mean: float
    sd: float

    @classmethod
    def from_samples(cls, samples: np.ndarray) -> "Percentiles":
        arr = np.asarray(samples, dtype=float)
        p10, p50, p90 = np.percentile(arr, [10, 50, 90])
        return cls(
            p10=float(p10), p50=float(p50), p90=float(p90),
            mean=float(arr.mean()), sd=float(arr.std(ddof=1)) if arr.size > 1 else 0.0,
        )

    @property
    def relative_width(self) -> float:
        """(p90 - p10) / p50 - a scale-free read on how uncertain this quantity is."""
        return float("inf") if self.p50 == 0 else (self.p90 - self.p10) / self.p50

    def as_dict(self) -> dict[str, float]:
        return {"p10": round(self.p10, 4), "p50": round(self.p50, 4),
                "p90": round(self.p90, 4), "mean": round(self.mean, 4),
                "sd": round(self.sd, 4)}


@dataclass(frozen=True)
class NutrientResult:
    tagname: str
    name: str
    unit: str
    per_serving: Percentiles
    per_100g: Percentiles
    worst_confidence: str
    borrowed_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "tagname": self.tagname, "name": self.name, "unit": self.unit,
            "per_serving": self.per_serving.as_dict(),
            "per_100g": self.per_100g.as_dict(),
            "worst_confidence": self.worst_confidence,
            "borrowed_count": self.borrowed_count,
        }


@dataclass
class IngredientDraw:
    """One leaf ingredient with its sampled mass across the Monte Carlo batch."""

    food_id: int
    food_name: str
    food_group: str
    grams: np.ndarray                 # shape (n_samples,)
    cooking_method: str | None
    preparation_state: str | None
    depth: int
    via_sub_template: str | None = None

    def scaled(self, factor: np.ndarray) -> "IngredientDraw":
        return IngredientDraw(
            food_id=self.food_id, food_name=self.food_name, food_group=self.food_group,
            grams=self.grams * factor, cooking_method=self.cooking_method,
            preparation_state=self.preparation_state, depth=self.depth,
            via_sub_template=self.via_sub_template,
        )


@dataclass
class ComputeResult:
    template_pathyam_id: str
    food_name: str
    n_samples: int
    seed: int
    engine_version: str
    servings: float
    portions: float
    mass_without_composition: float
    raw_mass_g: Percentiles
    cooked_mass_g: Percentiles
    nutrients: dict[str, NutrientResult]
    dominant_uncertainty_param: str | None
    variance_contributions: dict[str, float]
    ingredients: list[dict[str, Any]]
    sources: list[SourceRef]
    worst_confidence: str
    parameter_summary: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    qc: list[Any] = field(default_factory=list)

    @property
    def energy(self) -> NutrientResult | None:
        return self.nutrients.get("ENERC_KCAL")

    @property
    def uncleared_sources(self) -> list[SourceRef]:
        """Sources without commercial clearance. Non-empty blocks a release."""
        return [s for s in self.sources if not s.is_commercial_cleared]

    def as_dict(self) -> dict[str, Any]:
        return {
            "template": self.template_pathyam_id,
            "food_name": self.food_name,
            "n_samples": self.n_samples,
            "seed": self.seed,
            "engine_version": self.engine_version,
            "servings": self.servings,
            "portions": self.portions,
            "mass_without_composition": round(self.mass_without_composition, 4),
            "raw_mass_g": self.raw_mass_g.as_dict(),
            "cooked_mass_g": self.cooked_mass_g.as_dict(),
            "nutrients": {k: v.as_dict() for k, v in self.nutrients.items()},
            "dominant_uncertainty_param": self.dominant_uncertainty_param,
            "variance_contributions": {
                k: round(v, 4) for k, v in self.variance_contributions.items()
            },
            "ingredients": self.ingredients,
            "sources": [
                {"source_key": s.source_key, "licence": s.licence,
                 "commercial_cleared": s.is_commercial_cleared}
                for s in self.sources
            ],
            "worst_confidence": self.worst_confidence,
            "parameter_summary": self.parameter_summary,
            "warnings": self.warnings,
            "qc": [q.as_dict() for q in self.qc],
        }

    def summary_line(self) -> str:
        """The one-line rendering the app shows. Interval, never a bare point estimate."""
        e = self.energy
        if e is None:
            return f"{self.food_name}: energy not computable"
        s = e.per_serving
        line = (f"{self.food_name}: {s.p50:.0f} kcal "
                f"({s.p10:.0f}-{s.p90:.0f}, 80% CI)")
        if self.dominant_uncertainty_param:
            line += f"  |  largest uncertainty: {self.dominant_uncertainty_param}"
        return line
