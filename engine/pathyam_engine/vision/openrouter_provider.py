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
import datetime as dt
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Sequence

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
    "VisionModelUnavailable",
    "VisionModel",
    "MOCK_MODEL_VERSION",
    "FREE_TIER_WARNING",
    "verify_model_id",
    "list_vision_models",
    "fetch_catalogue",
    "usable_models",
    "select_vision_model",
    "resolve_model",
    "PREFER_FREE",
    "PREFER_CHEAPEST",
    "PREFER_ANY",
]

# Stamped on anything the mock produces. Callers and stored records can test for this
# to be sure they are not treating a canned observation as a real one.
MOCK_MODEL_VERSION = "mock"

_MODEL_ENV = "PATHYAM_VISION_MODEL"
_MOCK_ENV = "PATHYAM_MOCK_VISION"
_KEY_ENV = "OPENROUTER_API_KEY"
_BASE_ENV = "OPENROUTER_BASE_URL"
# Generic override, for any OpenAI-compatible gateway (OmniRoute, LiteLLM, a proxy).
_GATEWAY_ENV = "PATHYAM_VISION_BASE_URL"

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
    """The endpoint to call, with a production guard on gateway overrides.

    A compatible gateway is a development convenience: it can rewrite prompts in
    flight (see GATEWAY_COMPRESSION_WARNING), and its fallback across many upstream
    providers makes it hard to say which vendor received a given user's photograph
    -- which is precisely what `provider_may_train_on_input` and the
    `vision_third_party` consent purpose exist to state honestly.

    So an override is refused when PATHYAM_ENV=production. Failing at the first call
    is better than a deployment that silently routes patient meal photographs
    through a dev gateway because an env var survived a promotion. Same shape as the
    mailer refusing the console backend in production.
    """
    override = (os.environ.get(_GATEWAY_ENV, "").strip()
                or os.environ.get(_BASE_ENV, "").strip())
    if not override:
        return _DEFAULT_BASE_URL

    if (os.environ.get("PATHYAM_ENV", "development") == "production"
            and not is_openrouter(override)):
        raise VisionNotConfigured(
            f"{override!r} is a development gateway and PATHYAM_ENV=production. "
            "Gateways may rewrite prompts in flight and fan out across upstream "
            "providers, so the app cannot say which vendor received a user's "
            f"photograph. Unset {_GATEWAY_ENV} and point at OpenRouter directly."
        )
    return override


def is_openrouter(base_url: str) -> bool:
    """Whether this endpoint is OpenRouter itself, rather than a compatible gateway.

    Automatic selection reads OpenRouter's own catalogue fields -- `pricing`,
    `architecture.input_modalities`, `supported_parameters`, `expiration_date`. A
    different gateway serving /models will not carry the same shape, and ranking
    absent fields would silently pick badly rather than fail. So `auto` is refused
    off-OpenRouter and an explicit model id is required.
    """
    return "openrouter.ai" in base_url


# A gateway that rewrites the prompt in flight is a specific hazard for this module,
# not a general one, and it is worth naming because the feature is usually on by
# default and advertised as transparent.
#
# Two things in every request here are load-bearing and would not survive being
# paraphrased. The system prompt says "Do NOT calculate or guess nutrient figures"
# and "Do not substitute a plausible South Indian dish for one you cannot actually
# see". Those negations are the architectural boundary of the whole system -- the
# model perceives, the deterministic engine computes -- and rule-based prose
# compression is exactly the technique most likely to drop a negation. The other is
# MEAL_EXTRACTION_SCHEMA, which on a json_object model travels in the prompt as its
# only description of the contract.
#
# OmniRoute (github.com/diegosouzapw/OmniRoute) documents preservation guards and
# per-step fidelity gates for structured content, and a passthrough profile. Use
# them: point PATHYAM_VISION_BASE_URL at it with compression disabled for this
# route. Nothing here can detect a paraphrased instruction, so this is a
# configuration requirement, not something the client can enforce.
GATEWAY_COMPRESSION_WARNING = (
    "This provider's system prompt carries negative instructions ('do NOT calculate "
    "nutrients', 'do not substitute a dish you cannot see') that are the boundary "
    "between perception and computation. Disable prompt compression on the gateway "
    "for this route; a paraphrase that drops a negation cannot be detected here."
)


def _get_json(url: str, *, timeout: float, headers: dict[str, str] | None = None) -> Any:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


@dataclass(frozen=True)
class VisionModel:
    """One image-capable model from the OpenRouter catalogue."""

    id: str
    name: str
    context_length: int
    prompt_price: float          # USD per token
    completion_price: float
    image_price: float
    supports_structured_outputs: bool   # json_schema with strict:true
    supports_response_format: bool      # json_object at least
    expires_on: dt.date | None

    @property
    def is_router(self) -> bool:
        """OpenRouter's own meta-endpoints, which forward to some other model.

        `openrouter/free` selects at random from free models; `openrouter/auto`
        routes by community spend. Both are kept out of AUTOMATIC selection, not
        forbidden -- an operator can still name one.

        The reason is narrower than it first appeared, and worth stating accurately.
        Per OpenRouter's free-router guide the router does filter candidates by the
        capabilities a request needs, structured outputs included, so "it advertises
        a capability it cannot honour" overstates it. And provenance is no longer an
        objection at all: the response carries the model that actually answered, and
        that is what this module now records.

        What remains is empirical. The first live call through `openrouter/free`
        returned the bare string ``'User Safety: safe'`` instead of JSON, so the
        filtering is not airtight in practice. `auto` should pick something that
        works without a retry, so it picks a model that is accountable for its own
        capabilities. The docs also note free models carry lower rate limits and
        variable availability, which is a second reason not to make one the default
        an unattended deployment depends on.
        """
        return self.id.startswith("openrouter/")

    @property
    def has_variable_pricing(self) -> bool:
        """OpenRouter marks auto-routed models with a negative price.

        `openrouter/auto` reports -1 per token: the real cost depends on whichever
        model it routes to and is not knowable in advance. Read as a number it is
        cheaper than free, so a naive cheapest-first sort puts it first — at an
        unbounded actual price, and with the model that read the photograph decided
        somewhere else.
        """
        return (self.prompt_price < 0 or self.completion_price < 0
                or self.image_price < 0)

    @property
    def is_free(self) -> bool:
        return (not self.has_variable_pricing
                and self.prompt_price == 0.0
                and self.completion_price == 0.0
                and self.image_price == 0.0)

    @property
    def response_format_mode(self) -> str:
        """How much the model can be held to a schema.

        `json_schema` constrains the reply to MEAL_EXTRACTION_SCHEMA server-side.
        `json_object` only guarantees syntactically valid JSON, so the schema has to
        travel in the prompt and our own parser is the only check. Free models are
        overwhelmingly in the second group -- of the ten free image-capable models in
        the catalogue when this was written, two supported json_schema -- so refusing
        the weaker mode would leave almost no free option at all.
        """
        if self.supports_structured_outputs:
            return "json_schema"
        if self.supports_response_format:
            return "json_object"
        return "none"

    def cost_per_meal_usd(self, *, prompt_tokens: int = 1500, completion_tokens: int = 500) -> float:
        """Rough cost of one meal photo, or infinity when the price is not knowable.

        Image tokens dominate and vary by provider, so this orders models; it does
        not forecast a bill. Variable-priced models sort last rather than first.
        """
        if self.has_variable_pricing:
            return float("inf")
        return (self.prompt_price * prompt_tokens
                + self.completion_price * completion_tokens
                + self.image_price)


def _price(pricing: dict[str, Any], key: str) -> float:
    try:
        return float(pricing.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _parse_model(raw: dict[str, Any]) -> VisionModel | None:
    architecture = raw.get("architecture") or {}
    if "image" not in (architecture.get("input_modalities") or []):
        return None
    # Text out, and nothing but text. The catalogue lists music models
    # (google/lyria-*) as image-capable and priced at zero, so a filter that merely
    # required text among the outputs would rank a music generator at the top of the
    # free tier. A model whose repertoire includes audio is not a structured-extraction
    # model, and this provider only ever wants JSON back.
    if (architecture.get("output_modalities") or []) != ["text"]:
        return None
    if not raw.get("id"):
        return None

    params = set(raw.get("supported_parameters") or [])
    expires_raw = raw.get("expiration_date")
    expires_on = None
    if expires_raw:
        try:
            expires_on = dt.date.fromisoformat(str(expires_raw)[:10])
        except ValueError:
            expires_on = None

    pricing = raw.get("pricing") or {}
    return VisionModel(
        id=str(raw["id"]),
        name=str(raw.get("name") or raw["id"]),
        context_length=int(raw.get("context_length") or 0),
        prompt_price=_price(pricing, "prompt"),
        completion_price=_price(pricing, "completion"),
        image_price=_price(pricing, "image"),
        supports_structured_outputs="structured_outputs" in params,
        supports_response_format="response_format" in params,
        expires_on=expires_on,
    )


def fetch_catalogue(*, timeout: float = 10.0) -> list[VisionModel]:
    """Every image-capable, text-emitting model OpenRouter currently lists.

    Needs no API key, which is what lets a model id be checked before an account
    exists -- and is why the fabricated `gemini-3.7-flash` could not have survived
    this call.
    """
    try:
        payload = _get_json(f"{_base_url()}/models", timeout=timeout)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise VisionError(f"could not read the OpenRouter model catalogue: {exc}") from exc

    out = []
    for raw in payload.get("data") or []:
        model = _parse_model(raw)
        if model is not None:
            out.append(model)
    return out


def list_vision_models(*, timeout: float = 10.0) -> list[str]:
    """Ids of image-capable models, sorted. Kept for callers that only need names."""
    return sorted(m.id for m in fetch_catalogue(timeout=timeout))


# A model retiring inside this window is not worth selecting automatically: it will
# stop working with no code change, on a date nobody is watching. Free and preview
# models are where this bites -- when this was written, the only free model
# supporting strict schemas was due to expire in 22 days.
_EXPIRY_MARGIN = dt.timedelta(days=30)

PREFER_FREE = "free"
PREFER_CHEAPEST = "cheapest"
PREFER_ANY = "any"
_PREFERENCES = (PREFER_FREE, PREFER_CHEAPEST, PREFER_ANY)


def usable_models(
    catalogue: Sequence[VisionModel], *, today: dt.date | None = None
) -> list[VisionModel]:
    """Models this provider can actually drive.

    Two filters, both non-negotiable, and both about failing at configuration time
    rather than on a user's photograph:

    * it must be able to return JSON at all. A model with neither `json_schema` nor
      `json_object` returns prose, which raises VisionError on the first meal.
    * it must not be about to expire.
    * its price must be knowable. Auto-routed models report a negative price and
      decide elsewhere which model reads the photograph, so `model_version` on the
      stored observation would name a router rather than a reader.
    """
    today = today or dt.date.today()
    return [
        m for m in catalogue
        if m.response_format_mode != "none"
        and not m.is_router
        and not m.has_variable_pricing
        and (m.expires_on is None or m.expires_on > today + _EXPIRY_MARGIN)
    ]


def select_vision_model(
    catalogue: Sequence[VisionModel],
    *,
    prefer: str = PREFER_FREE,
    today: dt.date | None = None,
) -> VisionModel:
    """Choose a model. Deterministic, and it explains itself to the caller.

    `free` puts zero-cost models first and falls back to the cheapest paid one when
    no free model is usable, rather than failing -- a free tier that empties out
    overnight should degrade to a working paid call, not to no vision at all.

    Within a price tier, models that can be held to a strict schema rank above those
    that can only be asked for JSON, then larger context, then id for a stable
    tie-break. Nothing here consults quality: the catalogue does not report it, and
    inventing a ranking would be the same kind of guess as inventing a model id.
    """
    if prefer not in _PREFERENCES:
        raise ValueError(f"unknown preference {prefer!r}; expected one of {_PREFERENCES}")

    usable = usable_models(catalogue, today=today)
    if not usable:
        raise VisionModelUnavailable(
            "no OpenRouter model is usable: none accepts images, emits text, and can "
            "return JSON without expiring within 30 days"
        )

    def rank(m: VisionModel) -> tuple:
        return (0 if m.supports_structured_outputs else 1, -m.context_length, m.id)

    if prefer == PREFER_FREE:
        free = [m for m in usable if m.is_free]
        if free:
            return min(free, key=rank)
        # Fall through to cheapest rather than raising.
        prefer = PREFER_CHEAPEST

    if prefer == PREFER_CHEAPEST:
        return min(usable, key=lambda m: (m.cost_per_meal_usd(),) + rank(m))

    return min(usable, key=rank)


def resolve_model(
    spec: str, *, timeout: float = 10.0, today: dt.date | None = None
) -> tuple[VisionModel, str]:
    """Turn a PATHYAM_VISION_MODEL value into a concrete model, with a reason.

    `auto`, `auto:free`, `auto:cheapest` or `auto:any` select from the live
    catalogue. Anything else is treated as an explicit id and verified against the
    catalogue -- automatic selection never silently overrides an operator's choice.
    """
    spec = spec.strip()
    on_openrouter = is_openrouter(_base_url())

    # Short-circuit before any network call. Off OpenRouter there is no catalogue
    # worth fetching: `auto` cannot rank without its fields, and an explicit id
    # cannot be verified against a different gateway's /models.
    if not on_openrouter:
        if spec == "auto" or spec.startswith("auto:"):
            raise VisionNotConfigured(
                f"{spec!r} needs OpenRouter's catalogue: selection ranks on its "
                "pricing, modality and capability fields, which a compatible "
                f"gateway does not carry. Set {_MODEL_ENV} to an explicit model id "
                f"when {_GATEWAY_ENV} points elsewhere."
            )
        return (
            VisionModel(id=spec, name=spec, context_length=0,
                        prompt_price=0.0, completion_price=0.0, image_price=0.0,
                        supports_structured_outputs=False,
                        supports_response_format=True, expires_on=None),
            f"configured explicitly on a non-OpenRouter gateway: {spec}",
        )

    catalogue = fetch_catalogue(timeout=timeout)

    if spec == "auto" or spec.startswith("auto:"):
        if not on_openrouter:
            raise VisionNotConfigured(
                f"{spec!r} needs OpenRouter's catalogue: selection ranks on its "
                "pricing, modality and capability fields, which a compatible "
                f"gateway does not carry. Set {_MODEL_ENV} to an explicit model id "
                f"when {_GATEWAY_ENV} points elsewhere."
            )
        prefer = spec.split(":", 1)[1] if ":" in spec else PREFER_FREE
        chosen = select_vision_model(catalogue, prefer=prefer, today=today)
        tier = "free" if chosen.is_free else f"${chosen.cost_per_meal_usd():.4f}/meal (est.)"
        return chosen, (
            f"auto-selected {chosen.id} — {tier}, {chosen.response_format_mode}, "
            f"{chosen.context_length} ctx"
        )

    by_id = {m.id: m for m in catalogue}
    if spec not in by_id:
        near = [m.id for m in catalogue if spec.split("/")[-1].split(":")[0] in m.id]
        hint = f" Did you mean one of: {', '.join(near[:5])}?" if near else ""
        raise VisionModelUnavailable(
            f"{spec!r} is not an OpenRouter model that accepts images "
            f"({len(catalogue)} do).{hint}"
        )

    chosen = by_id[spec]
    if chosen.response_format_mode == "none":
        raise VisionModelUnavailable(
            f"{spec!r} accepts images but supports neither json_schema nor "
            "json_object, so it cannot be held to the extraction schema"
        )
    return chosen, f"configured explicitly: {chosen.id}"


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


# Free endpoints are free because the provider may use what you send. OpenRouter's
# own documentation says most free endpoints train on, or may publish, their inputs,
# and the applicable policy is the downstream provider's, not OpenRouter's.
#
# What this provider sends is a photograph a user took of their own meal, usually at
# home. That is personal data about an identifiable person's diet, adjacent to health
# data this codebase already treats carefully -- app.cgm_reading has its own consent
# purpose for exactly this reason. Selecting a free model is therefore a privacy
# decision as well as a cost one, and the warning travels with the selection so it
# reaches an operator rather than living in a comment.
FREE_TIER_WARNING = (
    "free OpenRouter endpoints may train on or publish the inputs they receive. "
    "Meal photographs are personal data. Set PATHYAM_VISION_MODEL to a paid model "
    "id, or turn off training-permitted routing in your OpenRouter privacy settings, "
    "before pointing real users at this."
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
        # Filled in on first use. Cached for the life of the provider: the catalogue
        # is a network call, and re-selecting per request would let the model change
        # mid-session, so two photographs in one sitting could be read by different
        # models with nothing recording the switch.
        self._resolved: VisionModel | None = None
        self._resolution_note: str | None = None

    @property
    def is_auto(self) -> bool:
        spec = (self.model_name or "").strip()
        return spec == "auto" or spec.startswith("auto:")

    def resolve(self) -> tuple[VisionModel, str]:
        """The concrete model this provider will call, and why it was chosen.

        Automatic selection did NOT reintroduce a default. An unset
        PATHYAM_VISION_MODEL is still unconfigured and still raises: "auto" is an
        explicit instruction to choose from the live catalogue, which is a different
        thing from a hardcoded guess. The catalogue cannot yield a model that does
        not exist, cannot see, or cannot return JSON; a hardcoded string can, and did.
        """
        if self._resolved is None:
            if not self.model_name:
                raise VisionNotConfigured(
                    f"no vision model configured: set {_MODEL_ENV} to a model id, or "
                    'to "auto" (or "auto:free" / "auto:cheapest") to select one from '
                    "the live catalogue. Run `python3 -m pathyam_engine.vision --list` "
                    "to see what is available. There is deliberately no hardcoded "
                    "default -- the previous one, 'gemini-3.7-flash', was not a real "
                    "model when it shipped and every call failed silently."
                )
            self._resolved, self._resolution_note = resolve_model(
                self.model_name, timeout=self.timeout)
        return self._resolved, self._resolution_note or ""

    @property
    def is_mocked(self) -> bool:
        return os.environ.get(_MOCK_ENV, "").strip() not in ("", "0", "false", "False")

    async def analyse_meal(self, image_bytes: bytes) -> MealObservation:
        if self.is_mocked:
            return self._generate_mock_observation(image_bytes)

        # A key is required for OpenRouter itself. A local gateway holds the upstream
        # credential and authenticates its own callers however it likes — often not at
        # all on localhost — and forwarding OpenRouter's key to it is simply wrong:
        # OmniRoute answers 401 "Invalid API key" because the key is not its.
        if not self.api_key and is_openrouter(self.base_url):
            raise VisionNotConfigured(
                f"no OpenRouter API key: set {_KEY_ENV}, or set {_MOCK_ENV}=1 to use "
                "the offline mock (which is clearly labelled as such)"
            )
        # Resolves "auto" against the live catalogue, or verifies an explicit id.
        # Either way this raises rather than proceeding with a model that does not
        # exist, cannot see, or cannot be held to the schema.
        model, _ = await asyncio.to_thread(self.resolve)

        try:
            # urllib is blocking; a VLM round trip on the event loop stalls every
            # other in-flight request in the worker for its duration.
            raw_text, served_by = await asyncio.to_thread(
                self._post_completion, image_bytes, model)
        except VisionError:
            raise
        except Exception as exc:
            # Deliberately not falling back to the mock. A fabricated meal that the
            # caller cannot distinguish from a real reading is worse than an error.
            raise VisionError(
                f"vision call to {model.id!r} failed: {type(exc).__name__}: {exc}"
            ) from exc

        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise VisionError(
                f"{model.id!r} did not return JSON: {raw_text[:200]!r}"
            ) from exc
        # Stamped with the model that ANSWERED, taken from the response — not the
        # spec that was configured, and not the id that was asked for. "auto" in a
        # stored record would say nothing about what read the photograph, and neither
        # would a router's own name.
        return self._parse_observation_data(data, raw_text, served_by)

    # ------------------------------------------------------------- transport --

    def _post_completion(self, image_bytes: bytes, model: VisionModel) -> tuple[str, str]:
        """One chat/completions call. Returns ``(content, model_that_answered)``.

        The response carries the model that actually served the request, which is not
        always the one asked for -- OpenRouter's routers forward to a model chosen per
        request. Reading it back is what lets an observation record what read the
        photograph rather than what was configured.
        """
        data_uri = "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode("ascii")

        # Held to the schema as tightly as the chosen model allows. json_schema is
        # enforced by the provider; json_object only promises valid JSON, so the
        # schema has to travel in the system prompt and our parser is the only check.
        # Most free models are in the second group, and sending them a json_schema
        # request they do not support is an error, not a graceful downgrade.
        mode = model.response_format_mode
        if mode == "json_schema":
            response_format: dict[str, Any] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "meal_extraction",
                    "strict": True,
                    "schema": MEAL_EXTRACTION_SCHEMA,
                },
            }
            system = SYSTEM_INSTRUCTION
        else:
            response_format = {"type": "json_object"}
            system = (
                SYSTEM_INSTRUCTION
                + "\n\nReturn JSON matching exactly this schema:\n"
                + json.dumps(MEAL_EXTRACTION_SCHEMA)
            )

        body = {
            "model": model.id,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": system},
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
            "response_format": response_format,
        }
        headers = {
            "Content-Type": "application/json",
            # Attribution only; neither carries user data. X-OpenRouter-Title is the
            # documented name (X-Title is accepted as an alias).
            "HTTP-Referer": "https://pathyam.app",
            "X-OpenRouter-Title": "Pathyam",
        }
        # Only sent when there is one. A gateway that needs no caller auth must not
        # receive an upstream provider's key.
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

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
                f"OpenRouter returned {exc.code} for {model.id!r}: {detail}"
            ) from exc

        # OpenRouter reports upstream provider errors inside a 200 body, at the top
        # level AND inside individual choices (per the API reference, an ErrorResponse
        # with code/message/metadata can appear in a choice). Checking only the top
        # level would surface a provider failure as an empty meal, as though the model
        # had genuinely seen nothing on the plate.
        if payload.get("error"):
            raise VisionError(f"OpenRouter error for {model.id!r}: {payload['error']}")

        choices = payload.get("choices") or []
        if not choices:
            raise VisionError(
                f"OpenRouter returned no choices for {model.id!r}: {str(payload)[:200]}"
            )
        if choices[0].get("error"):
            raise VisionError(
                f"OpenRouter error for {model.id!r}: {choices[0]['error']}")

        # What actually answered. A router forwards to a model chosen per request, so
        # this is the only honest value for model_version.
        served_by = str(payload.get("model") or model.id)

        content = (choices[0].get("message") or {}).get("content")
        if not content:
            raise VisionError(
                f"{model.id!r} returned an empty message; "
                f"finish_reason={choices[0].get('finish_reason')!r}"
            )
        return content, served_by

    # ----------------------------------------------------------- translation --

    def _parse_observation_data(
        self, data: dict[str, Any], raw_text: str, model_id: str
    ) -> MealObservation:
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
            model_version=model_id,
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
