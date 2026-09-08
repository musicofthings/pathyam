"""Pathyam Vision Engine Package."""

from .openrouter_provider import (FREE_TIER_WARNING, MOCK_MODEL_VERSION,
                                  PREFER_ANY, PREFER_CHEAPEST, PREFER_FREE,
                                  OpenRouterVisionProvider, VisionError,
                                  VisionModel, VisionModelUnavailable,
                                  VisionNotConfigured, fetch_catalogue,
                                  list_vision_models, resolve_model,
                                  select_vision_model, usable_models,
                                  verify_model_id)
from .protocol import (
    ImageQualityGateResult,
    MealObservation,
    PortionEstimate,
    VisionProvider,
    VisualItemObservation,
)
from .quality_gate import ImageQualityGate, evaluate_image_quality

__all__ = [
    "VisionProvider",
    "OpenRouterVisionProvider",
    "VisionError",
    "VisionNotConfigured",
    "VisionModelUnavailable",
    "VisionModel",
    "MOCK_MODEL_VERSION",
    "FREE_TIER_WARNING",
    "PREFER_FREE",
    "PREFER_CHEAPEST",
    "PREFER_ANY",
    "verify_model_id",
    "list_vision_models",
    "fetch_catalogue",
    "usable_models",
    "select_vision_model",
    "resolve_model",
    "PortionEstimate",
    "VisualItemObservation",
    "MealObservation",
    "ImageQualityGateResult",
    "ImageQualityGate",
    "evaluate_image_quality",
]
