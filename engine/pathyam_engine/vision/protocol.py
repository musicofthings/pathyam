"""VisionProvider protocol and dataclasses for Meal Vision Extraction.

Adheres strictly to the architectural separation rule:
  LLMs/VLMs perceive and explain; your deterministic engine resolves, calculates,
  validates and predicts.

The VLM NEVER emits calorie or nutrient values. It returns structured visual
observations with portion priors and uncertainty bounds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence

__all__ = [
    "PortionEstimate",
    "VisualItemObservation",
    "MealObservation",
    "ImageQualityGateResult",
    "VisionProvider",
]


@dataclass(frozen=True)
class PortionEstimate:
    grams: float | None = None
    millilitres: float | None = None
    uncertainty: str = "medium"  # "low" | "medium" | "high"
    min_grams: float | None = None
    max_grams: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "grams": self.grams,
            "millilitres": self.millilitres,
            "uncertainty": self.uncertainty,
            "min_grams": self.min_grams,
            "max_grams": self.max_grams,
        }


@dataclass(frozen=True)
class VisualItemObservation:
    visual_label: str
    estimated_portion: PortionEstimate
    preparation: str = "unknown"
    count: int | None = None
    modifiers: list[str] = field(default_factory=list)
    confidence: float = 0.85

    def as_dict(self) -> dict[str, Any]:
        return {
            "visual_label": self.visual_label,
            "estimated_portion": self.estimated_portion.as_dict(),
            "preparation": self.preparation,
            "count": self.count,
            "modifiers": self.modifiers,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class MealObservation:
    # model_version is required and has no default. It used to default to
    # "gemini-3.7-flash" -- a model that does not exist -- so an observation
    # constructed without one was stamped with a fabricated provenance. Whatever
    # produced the observation must say so, including the mock (which says "mock").
    items: list[VisualItemObservation]
    model_version: str
    raw_vlm_response: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "items": [item.as_dict() for item in self.items],
            "model_version": self.model_version,
        }


@dataclass(frozen=True)
class ImageQualityGateResult:
    passed: bool
    quality_score: float  # 0.0 - 1.0
    issues: list[str] = field(default_factory=list)


class VisionProvider(Protocol):
    """Abstract interface for VLM meal extraction."""

    async def analyse_meal(self, image_bytes: bytes) -> MealObservation:
        ...
