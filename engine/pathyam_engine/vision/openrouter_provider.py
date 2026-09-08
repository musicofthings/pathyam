"""OpenRouter vision provider for meal photo extraction.

Extracts visual food observations and portion priors. It never computes calories or
nutrient values -- that is the deterministic engine's job, and the separation is what
keeps a model from inventing a number that looks computed.

WHY OPENROUTER RATHER THAN A SINGLE VENDOR
------------------------------------------
The bug this module was rewritten to prevent was a *fabricated model id*: the original
default, ``gemini-3.7-flash``, is not a real model, so every live call failed -- and
the failure was swallowed into a canned observation, so nobody noticed. Nothing about
that string could be checked without a Google account.

OpenRouter publishes its catalogue at an unauthenticated endpoint, so
:func:`verify_model_id` can check a configured id against reality *before* a key is
issued or a token is spent -- including whether the model accepts images at all,
which is the other way this call can be silently misconfigured. That check is the
main reason this is an improvement rather than a lateral move; the second is that
swapping vision models to compare them on the golden meal set is a config change.

TWO THINGS THIS MODULE STILL DELIBERATELY REFUSES TO DO
-------------------------------------------------------
**It does not guess a model id.** There is no default. ``PATHYAM_VISION_MODEL`` must
name a model the operator has checked -- ``python -m pathyam_engine.vision`` runs the
check. Unset means unconfigured, and unconfigured raises.

**It does not fall back to the mock on failure.** A caller cannot distinguish a
fabricated meal from a real reading, so failures raise :class:`VisionError`. The mock
is reachable only by setting ``PATHYAM_MOCK_VISION=1``, and what it returns is stamped
``model_version="mock"`` so it cannot be mistaken for a measurement downstream.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import urllib.error
import urllib.request
from typing import Any

from .protocol import (
    MealObservation,
    PortionEstimate,
    VisionProvider,
    VisualItemObservation,
)

__all__ = [
    "OpenRouterVisionProvider",
    "VisionError",
    "VisionNotConfigured",
    "MOCK_MODEL_VERSION",
    "verify_model_id",
    "list_vision_models",
]

# Stamped on anything the mock produces. Callers and stored records can test for this
# to be sure they are not treating a canned observation as a real one.
MOCK_MODEL_VERSION = "mock"

_MODEL_ENV = "PATHYAM_VISION_MODEL"
_MOCK_ENV = "PATHYAM_MOCK_VISION"
_KEY_ENV = "OPENROUTER_API_KEY"
_BASE_ENV = "OPENROUTER_BASE_URL"

_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


class VisionError(RuntimeError):
    """A vision call was attempted and failed. Never swallowed into a mock."""


class VisionNotConfigured(VisionError):
    """No API key, or no model id. Configuration problem, not a transient failure."""


class VisionModelUnavailable(VisionNotConfigured):
    """The configured model id is not in the catalogue, or cannot accept an image."""


# Standard JSON Schema, which is what OpenAI-compatible `response_format` takes. The
# previous version carried Google's dialect (uppercase "OBJECT", `nullable: true`);
# sending that to an OpenAI-compatible endpoint is rejected or, worse, ignored.
#
# `strict` schemas forbid optional keys, so every property is required and the
# nullable ones are typed as a union with "null" rather than omitted.
MEAL_EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "visual_label", "preparation", "count", "confidence",
                    "modifiers", "estimated_portion",
                ],
                "properties": {
                    "visual_label": {"type": "string"},
                    "preparation": {"type": "string"},
                    "count": {"type": ["integer", "null"]},
                    "confidence": {"type": "number"},
                    "modifiers": {"type": "array", "items": {"type": "string"}},
                    "estimated_portion": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "grams", "millilitres", "uncertainty",
                            "min_grams", "max_grams",
                        ],
                        "properties": {
                            "grams": {"type": ["number", "null"]},
                            "millilitres": {"type": ["number", "null"]},
                            "uncertainty": {
                                "type": "string",
                                "enum": ["low", "medium", "high"],
                            },
                            "min_grams": {"type": ["number", "null"]},
                            "max_grams": {"type": ["number", "null"]},
                        },
                    },
                },
            },
        }
    },
}

SYSTEM_INSTRUCTION = """You are a specialized clinical food vision extractor.
Your job is ONLY to observe food items in the photograph, estimate portion masses/volumes with realistic uncertainty bounds, and describe preparations.

CRITICAL RULES:
1. Do NOT calculate or guess nutrient figures, calories, protein, fat, or carbohydrates.
2. Output JSON strictly matching the specified JSON Schema.
3. Express portion as a point estimate + realistic min/max range + uncertainty level.
4. If you cannot identify an item, say so in visual_label and lower its confidence.
   Do not substitute a plausible South Indian dish for one you cannot actually see.
"""


def _base_url() -> str:
    return os.environ.get(_BASE_ENV, "").strip() or _DEFAULT_BASE_URL


def _get_json(url: str, *, timeout: float, headers: dict[str, str] | None = None) -> Any:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def list_vision_models(*, timeout: float = 10.0) -> list[str]:
    """Ids in the OpenRouter catalogue that accept image input.

    The catalogue endpoint needs no API key, which is what makes
    :func:`verify_model_id` usable before an account exists.
    """
    try:
        payload = _get_json(f"{_base_url()}/models", timeout=timeout)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise VisionError(f"could not read the OpenRouter model catalogue: {exc}") from exc

    out = []
    for model in payload.get("data") or []:
        modalities = (model.get("architecture") or {}).get("input_modalities") or []
        if "image" in modalities and model.get("id"):
            out.append(str(model["id"]))
    return sorted(out)


def verify_model_id(model_id: str, *, timeout: float = 10.0) -> None:
    """Raise unless ``model_id`` exists in the catalogue and accepts images.

    Both halves matter. A nonexistent id is the failure that shipped. A real but
    text-only id is the same failure wearing a plausible name -- it would be accepted
    at configuration time and fail on the first photograph.
    """
    available = list_vision_models(timeout=timeout)
    if model_id in available:
        return
    near = [m for m in available if model_id.split("/")[-1].split(":")[0] in m]
    hint = f" Did you mean one of: {', '.join(near[:5])}?" if near else ""
    raise VisionModelUnavailable(
        f"{model_id!r} is not an OpenRouter model that accepts images "
        f"({len(available)} do).{hint}"
    )


class OpenRouterVisionProvider(VisionProvider):
    """OpenRouter VLM adapter. Requires an API key and an explicit model id."""

    def __init__(
        self,
        api_key: str | None = None,
        model_name: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.api_key = api_key or os.environ.get(_KEY_ENV)
        self.model_name = model_name or os.environ.get(_MODEL_ENV)
        self.base_url = (base_url or _base_url()).rstrip("/")
        self.timeout = timeout

    @property
    def is_mocked(self) -> bool:
        return os.environ.get(_MOCK_ENV, "").strip() not in ("", "0", "false", "False")

    async def analyse_meal(self, image_bytes: bytes) -> MealObservation:
        if self.is_mocked:
            return self._generate_mock_observation(image_bytes)

        if not self.api_key:
            raise VisionNotConfigured(
                f"no OpenRouter API key: set {_KEY_ENV}, or set {_MOCK_ENV}=1 to use "
                "the offline mock (which is clearly labelled as such)"
            )
        if not self.model_name:
            raise VisionNotConfigured(
                f"no vision model configured: set {_MODEL_ENV} to a model id you have "
                "verified with `python -m pathyam_engine.vision`. There is deliberately "
                "no default -- the previous default, 'gemini-3.7-flash', was not a real "
                "model and every call failed silently."
            )

        try:
            # urllib is blocking; a VLM round trip on the event loop stalls every
            # other in-flight request in the worker for its duration.
            raw_text = await asyncio.to_thread(self._post_completion, image_bytes)
        except VisionError:
            raise
        except Exception as exc:
            # Deliberately not falling back to the mock. A fabricated meal that the
            # caller cannot distinguish from a real reading is worse than an error.
            raise VisionError(
                f"vision call to {self.model_name!r} failed: {type(exc).__name__}: {exc}"
            ) from exc

        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise VisionError(
                f"{self.model_name!r} did not return JSON: {raw_text[:200]!r}"
            ) from exc
        return self._parse_observation_data(data, raw_text)

    # ------------------------------------------------------------- transport --

    def _post_completion(self, image_bytes: bytes) -> str:
        """One chat/completions call. Returns the raw message content."""
        data_uri = "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode("ascii")
        body = {
            "model": self.model_name,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": SYSTEM_INSTRUCTION},
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_uri}},
                        {"type": "text", "text": (
                            "Extract all visible food items, preparation methods, and "
                            "estimated portions with uncertainty intervals."
                        )},
                    ],
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "meal_extraction",
                    "strict": True,
                    "schema": MEAL_EXTRACTION_SCHEMA,
                },
            },
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            # OpenRouter uses these for attribution only; neither carries user data.
            "HTTP-Referer": "https://pathyam.app",
            "X-Title": "Pathyam",
        }
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            raise VisionError(
                f"OpenRouter returned {exc.code} for {self.model_name!r}: {detail}"
            ) from exc

        # OpenRouter reports upstream provider errors inside a 200 body.
        if payload.get("error"):
            raise VisionError(f"OpenRouter error for {self.model_name!r}: {payload['error']}")

        choices = payload.get("choices") or []
        if not choices:
            raise VisionError(
                f"OpenRouter returned no choices for {self.model_name!r}: {str(payload)[:200]}"
            )
        content = (choices[0].get("message") or {}).get("content")
        if not content:
            raise VisionError(
                f"{self.model_name!r} returned an empty message; "
                f"finish_reason={choices[0].get('finish_reason')!r}"
            )
        return content

    # ----------------------------------------------------------- translation --

    def _parse_observation_data(self, data: dict[str, Any], raw_text: str) -> MealObservation:
        raw_items = data.get("items", [])
        obs_items: list[VisualItemObservation] = []

        for item in raw_items:
            portion_raw = item.get("estimated_portion") or {}
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
                    preparation=item.get("preparation") or "unknown",
                    count=item.get("count"),
                    modifiers=item.get("modifiers") or [],
                    confidence=float(item.get("confidence", 0.85)),
                )
            )

        return MealObservation(
            items=obs_items,
            model_version=self.model_name or "unconfigured",
            raw_vlm_response=raw_text,
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
                        grams=120.0, uncertainty="medium",
                        min_grams=90.0, max_grams=150.0,
                    ),
                    preparation="griddled",
                    count=1,
                    modifiers=["crispy"],
                    confidence=0.92,
                ),
                VisualItemObservation(
                    visual_label="sambar",
                    estimated_portion=PortionEstimate(
                        millilitres=140.0, grams=140.0, uncertainty="medium",
                        min_grams=100.0, max_grams=180.0,
                    ),
                    preparation="simmered",
                    count=1,
                    modifiers=["vegetable"],
                    confidence=0.88,
                ),
            ],
            model_version=MOCK_MODEL_VERSION,
            raw_vlm_response='{"mock": true}',
        )
