"""Gemini vision provider for meal photo extraction.

Extracts visual food observations and portion priors. It never computes calories or
nutrient values -- that is the deterministic engine's job, and the separation is what
keeps a model from inventing a number that looks computed.

TWO THINGS THIS MODULE DELIBERATELY REFUSES TO DO
-------------------------------------------------
**It does not guess a model id.** There is no default. The previous version defaulted
to "gemini-3.7-flash", which is not a Google model at all -- the Gemini 3 family uses
`gemini-3-*` ids, and 3.7 appears to have been borrowed from a different vendor's
version numbering. Every live call therefore failed. Rather than swap in another
unverified string, `PATHYAM_VISION_MODEL` must name a model the operator has checked
against their own account (`client.models.list()`). Unset means unconfigured, and
unconfigured raises.

**It does not fall back to the mock on failure.** The previous version wrapped the
API call in `except Exception: pass` and returned a hardcoded plate of dosa and
sambar at confidence 0.92. A caller could not tell that from a real reading, so a
user photographing any meal got fabricated portions that flowed into the compute
engine and into their food log. Failures now raise :class:`VisionError`. The mock is
reachable only by setting `PATHYAM_MOCK_VISION=1`, and what it returns is stamped
`model_version="mock"` so it cannot be mistaken for a measurement downstream.
"""

from __future__ import annotations

import json
import os
from typing import Any

from .protocol import (
    MealObservation,
    PortionEstimate,
    VisionProvider,
    VisualItemObservation,
)

__all__ = ["GeminiVisionProvider", "VisionError", "VisionNotConfigured", "MOCK_MODEL_VERSION"]

# Stamped on anything the mock produces. Callers and stored records can test for this
# to be sure they are not treating a canned observation as a real one.
MOCK_MODEL_VERSION = "mock"

_MODEL_ENV = "PATHYAM_VISION_MODEL"
_MOCK_ENV = "PATHYAM_MOCK_VISION"


class VisionError(RuntimeError):
    """A vision call was attempted and failed. Never swallowed into a mock."""


class VisionNotConfigured(VisionError):
    """No API key, or no model id. Configuration problem, not a transient failure."""

# JSON Schema for Gemini 3.7 Flash Structured Output
MEAL_EXTRACTION_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "items": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "visual_label": {"type": "STRING"},
                    "preparation": {"type": "STRING"},
                    "count": {"type": "INTEGER", "nullable": True},
                    "confidence": {"type": "NUMBER"},
                    "modifiers": {
                        "type": "ARRAY",
                        "items": {"type": "STRING"},
                    },
                    "estimated_portion": {
                        "type": "OBJECT",
                        "properties": {
                            "grams": {"type": "NUMBER", "nullable": True},
                            "millilitres": {"type": "NUMBER", "nullable": True},
                            "uncertainty": {
                                "type": "STRING",
                                "enum": ["low", "medium", "high"],
                            },
                            "min_grams": {"type": "NUMBER", "nullable": True},
                            "max_grams": {"type": "NUMBER", "nullable": True},
                        },
                        "required": ["uncertainty"],
                    },
                },
                "required": ["visual_label", "estimated_portion", "confidence"],
            },
        }
    },
    "required": ["items"],
}

SYSTEM_INSTRUCTION = """You are a specialized clinical food vision extractor.
Your job is ONLY to observe food items in the photograph, estimate portion masses/volumes with realistic uncertainty bounds, and describe preparations.

CRITICAL RULES:
1. Do NOT calculate or guess nutrient figures, calories, protein, fat, or carbohydrates.
2. Output JSON strictly matching the specified JSON Schema.
3. Express portion as a point estimate + realistic min/max range + uncertainty level.
"""


class GeminiVisionProvider(VisionProvider):
    """Gemini VLM adapter. Requires an API key and an explicit model id."""

    def __init__(
        self,
        api_key: str | None = None,
        model_name: str | None = None,
    ) -> None:
        self.api_key = (
            api_key
            or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
        )
        self.model_name = model_name or os.environ.get(_MODEL_ENV)

    @property
    def is_mocked(self) -> bool:
        return os.environ.get(_MOCK_ENV, "").strip() not in ("", "0", "false", "False")

    async def analyse_meal(self, image_bytes: bytes) -> MealObservation:
        if self.is_mocked:
            return self._generate_mock_observation(image_bytes)

        if not self.api_key:
            raise VisionNotConfigured(
                "no Gemini API key: set GEMINI_API_KEY, or set PATHYAM_MOCK_VISION=1 "
                "to use the offline mock (which is clearly labelled as such)"
            )
        if not self.model_name:
            raise VisionNotConfigured(
                f"no vision model configured: set {_MODEL_ENV} to a model id you have "
                "verified against your own account with client.models.list(). There is "
                "deliberately no default -- the previous default, 'gemini-3.7-flash', "
                "was not a real model and every call failed silently."
            )

        try:
            return await self._call_gemini_api(image_bytes)
        except VisionError:
            raise
        except Exception as exc:
            # Deliberately not falling back to the mock. A fabricated meal that the
            # caller cannot distinguish from a real reading is worse than an error.
            raise VisionError(
                f"vision call to {self.model_name!r} failed: {type(exc).__name__}: {exc}"
            ) from exc

    async def _call_gemini_api(self, image_bytes: bytes) -> MealObservation:
        # Import google.genai or google.generativeai if available
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=self.api_key)
            # client.aio is the async surface. The sync call blocks the event loop,
            # which in a FastAPI worker stalls every other in-flight request for the
            # duration of a VLM round trip.
            response = await client.aio.models.generate_content(
                model=self.model_name,
                contents=[
                    types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                    "Extract all visible food items, preparation methods, and estimated portions with uncertainty intervals.",
                ],
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    response_mime_type="application/json",
                    response_schema=MEAL_EXTRACTION_SCHEMA,
                    temperature=0.2,
                ),
            )
            raw_text = response.text or "{}"
            data = json.loads(raw_text)
            return self._parse_observation_data(data, raw_text)
        except ImportError as exc:
            raise VisionNotConfigured(
                "google-genai is not installed: pip install google-genai"
            ) from exc

    def _parse_observation_data(self, data: dict[str, Any], raw_text: str) -> MealObservation:
        raw_items = data.get("items", [])
        obs_items: list[VisualItemObservation] = []

        for item in raw_items:
            portion_raw = item.get("estimated_portion", {})
            g = portion_raw.get("grams")
            ml = portion_raw.get("millilitres")
            unc = portion_raw.get("uncertainty", "medium")
            min_g = portion_raw.get("min_grams")
            max_g = portion_raw.get("max_grams")

            if min_g is None and g is not None:
                min_g = max(0.0, g * 0.75)
            if max_g is None and g is not None:
                max_g = g * 1.30

            portion = PortionEstimate(
                grams=float(g) if g is not None else None,
                millilitres=float(ml) if ml is not None else None,
                uncertainty=str(unc),
                min_grams=float(min_g) if min_g is not None else None,
                max_grams=float(max_g) if max_g is not None else None,
            )

            obs_items.append(
                VisualItemObservation(
                    visual_label=item.get("visual_label", "unknown_food"),
                    estimated_portion=portion,
                    preparation=item.get("preparation", "unknown"),
                    count=item.get("count"),
                    modifiers=item.get("modifiers", []),
                    confidence=float(item.get("confidence", 0.85)),
                )
            )

        return MealObservation(
            items=obs_items,
            raw_vlm_response=raw_text,
            model_version=self.model_name,
        )

    def _generate_mock_observation(self, image_bytes: bytes) -> MealObservation:
        """A fixed, obviously fake observation for offline development.

        Reachable only via PATHYAM_MOCK_VISION. The returned observation is stamped
        ``model_version="mock"`` so that nothing downstream -- the compute engine, the
        meal log, a benchmark run -- can mistake it for something a model actually
        saw. It does not vary with the image, because it never looks at it.
        """
        return MealObservation(
            items=[
                VisualItemObservation(
                    visual_label="dosa plain",
                    estimated_portion=PortionEstimate(
                        grams=120.0,
                        uncertainty="medium",
                        min_grams=90.0,
                        max_grams=150.0,
                    ),
                    preparation="griddled",
                    count=1,
                    modifiers=["crispy"],
                    confidence=0.92,
                ),
                VisualItemObservation(
                    visual_label="sambar",
                    estimated_portion=PortionEstimate(
                        millilitres=140.0,
                        grams=140.0,
                        uncertainty="medium",
                        min_grams=100.0,
                        max_grams=180.0,
                    ),
                    preparation="simmered",
                    count=1,
                    modifiers=["vegetable"],
                    confidence=0.88,
                ),
            ],
            raw_vlm_response='{"mock": true}',
            model_version=MOCK_MODEL_VERSION,
        )
