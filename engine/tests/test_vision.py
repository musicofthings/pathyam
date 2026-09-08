"""Vision provider: configuration, failure behaviour, and the mock's labelling.

Every test here pins a property that a previous version of this module violated.
No test in this file makes a network call.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from pathyam_engine.vision import (
    MOCK_MODEL_VERSION,
    ImageQualityGate,
    MealObservation,
    OpenRouterVisionProvider,
    VisionError,
    VisionModelUnavailable,
    VisionNotConfigured,
    verify_model_id,
)
from pathyam_engine.vision import openrouter_provider as op

FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 200
# Not a credential. The provider never validates the key locally; these tests
# assert on configuration and parsing, and none of them reaches the network.
PLACEHOLDER_KEY = "x"


@pytest.mark.asyncio
async def test_image_quality_gate_checks_size_and_header():
    gate = ImageQualityGate(min_bytes=10, max_bytes=1000)

    # Too small
    res_small = gate.evaluate(b"small")
    assert not res_small.passed
    assert any("too small" in issue for issue in res_small.issues)

    # Valid size JPEG header
    fake_jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 100
    res_jpeg = gate.evaluate(fake_jpeg)
    assert res_jpeg.passed
    assert res_jpeg.quality_score == 1.0


@pytest.mark.asyncio
async def test_the_mock_is_labelled_so_it_cannot_pass_as_a_real_reading(monkeypatch):
    """The mock used to be stamped with the model id, indistinguishable from real."""
    monkeypatch.setenv("PATHYAM_MOCK_VISION", "1")
    provider = OpenRouterVisionProvider(api_key=PLACEHOLDER_KEY, model_name="unused")

    obs: MealObservation = await provider.analyse_meal(FAKE_JPEG)

    assert obs.model_version == MOCK_MODEL_VERSION == "mock"
    assert len(obs.items) >= 1

    first_item = obs.items[0]
    assert first_item.visual_label
    assert (first_item.estimated_portion.grams is not None
            or first_item.estimated_portion.millilitres is not None)
    assert first_item.estimated_portion.uncertainty in ("low", "medium", "high")
    assert first_item.confidence > 0.0

    # Ensure VLM response carries NO calorie or nutrient numbers directly
    obs_dict = obs.as_dict()
    assert "calories" not in str(obs_dict).lower()
    assert "kcal" not in str(obs_dict).lower()


def test_an_observation_cannot_be_built_without_saying_what_produced_it():
    """model_version used to default to 'gemini-3.7-flash', a model that never existed.

    Anything constructing an observation must state its provenance, so a record can
    never carry a fabricated one by omission.
    """
    with pytest.raises(TypeError):
        MealObservation(items=[])       # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_a_missing_api_key_raises_instead_of_returning_a_mock(monkeypatch):
    """Regression: this used to return a hardcoded plate of dosa and sambar."""
    monkeypatch.delenv("PATHYAM_MOCK_VISION", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    with pytest.raises(VisionNotConfigured, match="no OpenRouter API key"):
        await OpenRouterVisionProvider().analyse_meal(FAKE_JPEG)


@pytest.mark.asyncio
async def test_a_missing_model_id_raises_rather_than_defaulting(monkeypatch):
    """There is deliberately no default: the old one was not a real model."""
    monkeypatch.delenv("PATHYAM_MOCK_VISION", raising=False)
    monkeypatch.delenv("PATHYAM_VISION_MODEL", raising=False)

    provider = OpenRouterVisionProvider(api_key=PLACEHOLDER_KEY)
    with pytest.raises(VisionNotConfigured, match="no vision model configured"):
        await provider.analyse_meal(FAKE_JPEG)


@pytest.mark.asyncio
async def test_an_api_failure_raises_and_never_degrades_into_the_mock(monkeypatch):
    """The failure mode that made every meal photo return dosa and sambar."""
    monkeypatch.delenv("PATHYAM_MOCK_VISION", raising=False)

    monkeypatch.setattr(op, "_get_json", lambda url, **kw: _CATALOGUE)
    provider = OpenRouterVisionProvider(api_key=PLACEHOLDER_KEY, model_name="vendor/sees-images")

    def boom(_image, _model):
        raise RuntimeError("upstream 404: model not found")

    monkeypatch.setattr(provider, "_post_completion", boom)

    with pytest.raises(VisionError) as excinfo:
        await provider.analyse_meal(FAKE_JPEG)
    assert "vendor/sees-images" in str(excinfo.value), "the error must name the model tried"


@pytest.mark.asyncio
async def test_a_non_json_reply_raises_rather_than_yielding_an_empty_meal(monkeypatch):
    """json.loads used to run unguarded; a prose reply would have thrown a bare
    JSONDecodeError, and an empty dict would have produced a meal with no items."""
    monkeypatch.delenv("PATHYAM_MOCK_VISION", raising=False)
    monkeypatch.setattr(op, "_get_json", lambda url, **kw: _CATALOGUE)
    provider = OpenRouterVisionProvider(api_key=PLACEHOLDER_KEY, model_name="vendor/sees-images")
    monkeypatch.setattr(provider, "_post_completion",
                        lambda _i, m: ("I'm sorry, I can't identify this photo.", m.id))

    with pytest.raises(VisionError, match="did not return JSON"):
        await provider.analyse_meal(FAKE_JPEG)


def test_no_default_model_id_is_baked_into_the_provider(monkeypatch):
    """Guard against a fabricated default creeping back in.

    The model id must come from configuration. An unconfigured provider carries
    None and fails loudly at call time, rather than carrying a plausible-looking
    string that 404s on every request.
    """
    monkeypatch.delenv("PATHYAM_VISION_MODEL", raising=False)
    assert OpenRouterVisionProvider(api_key=PLACEHOLDER_KEY).model_name is None

    monkeypatch.setenv("PATHYAM_VISION_MODEL", "configured-by-operator")
    assert OpenRouterVisionProvider(api_key=PLACEHOLDER_KEY).model_name == "configured-by-operator"


# ------------------------------------------------- catalogue verification ----
#
# The point of moving to OpenRouter: a configured model id can be checked against a
# public catalogue before a key exists, and a model can be chosen from it
# automatically. These tests stub the catalogue fetch; none touches the network.


def _raw(model_id, *, modalities=("text", "image"), out=("text",), params=("structured_outputs", "response_format"),
         prompt="0", completion="0", image="0", ctx=100000, expires=None):
    return {
        "id": model_id,
        "name": model_id,
        "context_length": ctx,
        "architecture": {"input_modalities": list(modalities), "output_modalities": list(out)},
        "supported_parameters": list(params),
        "pricing": {"prompt": prompt, "completion": completion, "image": image},
        "expiration_date": expires,
    }


_CATALOGUE = {
    "data": [
        _raw("vendor/sees-images"),
        _raw("vendor/text-only", modalities=("text",)),
        _raw("other/also-sees", ctx=50000),
        _raw("paid/good", prompt="0.000002", completion="0.000008"),
        _raw("paid/dear", prompt="0.00002", completion="0.00008"),
    ]
}


@pytest.fixture
def catalogue(monkeypatch):
    monkeypatch.setattr(op, "_get_json", lambda url, **kw: _CATALOGUE)


def _cat(*raws):
    return [m for m in (op._parse_model(r) for r in raws) if m is not None]


def test_verify_accepts_a_model_that_exists_and_takes_images(catalogue):
    verify_model_id("vendor/sees-images")     # does not raise


def test_verify_rejects_a_model_id_that_does_not_exist(catalogue):
    """The exact bug that shipped: 'gemini-3.7-flash' was not a real model then.

    (It is now — Google shipped it later. A guess that comes true a year on is
    still a guess, and it failed every call for as long as it was in the code.)
    """
    with pytest.raises(VisionModelUnavailable, match="gemini-3.7-flash"):
        verify_model_id("google/gemini-3.7-flash")


def test_verify_rejects_a_real_model_that_cannot_see(catalogue):
    """The same failure wearing a plausible name.

    A text-only id passes an existence check and then fails on the first
    photograph, at runtime, in front of a user.
    """
    with pytest.raises(VisionModelUnavailable, match="accepts images"):
        verify_model_id("vendor/text-only")


def test_a_rejection_suggests_near_matches(catalogue):
    with pytest.raises(VisionModelUnavailable, match="Did you mean"):
        verify_model_id("wrongvendor/sees-images")


def test_an_unreachable_catalogue_is_an_error_not_an_empty_allowlist(monkeypatch):
    """Failing open would let any id through whenever the network is down."""
    def unreachable(url, **kw):
        raise OSError("network unreachable")
    monkeypatch.setattr(op, "_get_json", unreachable)

    with pytest.raises(VisionError, match="could not read the OpenRouter model catalogue"):
        verify_model_id("vendor/sees-images")


# ------------------------------------------------------ automatic selection ----

TODAY = dt.date(2026, 9, 8)


def test_free_is_preferred_over_paid():
    chosen = op.select_vision_model(
        _cat(_raw("paid/one", prompt="0.000001", completion="0.000001"),
             _raw("free/one")),
        prefer=op.PREFER_FREE, today=TODAY)
    assert chosen.id == "free/one"
    assert chosen.is_free


def test_a_music_model_priced_at_zero_does_not_win_the_free_tier():
    """google/lyria-* are listed image-capable at zero cost, and emit audio.

    A filter that merely required text among the output modalities put a music
    generator at the top of a free-first ranking.
    """
    chosen = op.select_vision_model(
        _cat(_raw("google/lyria-3-pro-preview", out=("text", "audio"), ctx=1048576),
             _raw("real/vision", ctx=1000)),
        prefer=op.PREFER_FREE, today=TODAY)
    assert chosen.id == "real/vision"


def test_an_auto_router_never_wins_on_price():
    """OpenRouter marks auto-routed models with a negative per-token price.

    Read as a number that is cheaper than free, so cheapest-first put it first — at
    an unbounded real cost, and with the model that read the photograph chosen
    somewhere else, so model_version would name a router rather than a reader.
    """
    catalogue = _cat(_raw("openrouter/auto", prompt="-1", completion="-1", ctx=2000000),
                     _raw("paid/known", prompt="0.00001", completion="0.00001"))
    assert [m.id for m in op.usable_models(catalogue, today=TODAY)] == ["paid/known"]
    assert op.select_vision_model(catalogue, prefer=op.PREFER_CHEAPEST, today=TODAY).id == "paid/known"


def test_a_model_that_cannot_return_json_is_never_selected():
    """Of the ten free image-capable models in the live catalogue, most support no
    JSON mode at all. Selecting one returns prose on the first meal photo."""
    catalogue = _cat(_raw("free/prose", params=("temperature",)),
                     _raw("paid/json", prompt="0.00001", completion="0.00001"))
    assert [m.id for m in op.usable_models(catalogue, today=TODAY)] == ["paid/json"]
    assert op.select_vision_model(catalogue, prefer=op.PREFER_FREE, today=TODAY).id == "paid/json"


def test_a_model_expiring_soon_is_not_selected_automatically():
    """It stops working on a date nobody is watching, with no code change.

    This is not hypothetical: the only free model supporting strict schemas when
    this was written was due to expire 22 days later.
    """
    catalogue = _cat(_raw("free/expiring", expires="2026-09-30"),
                     _raw("free/durable"))
    assert op.select_vision_model(catalogue, prefer=op.PREFER_FREE, today=TODAY).id == "free/durable"


def test_free_preference_falls_back_to_paid_rather_than_failing():
    """A free tier that empties overnight should degrade to a working paid call,
    not to no vision at all."""
    chosen = op.select_vision_model(
        _cat(_raw("paid/cheap", prompt="0.000001", completion="0.000001"),
             _raw("paid/dear", prompt="0.001", completion="0.001")),
        prefer=op.PREFER_FREE, today=TODAY)
    assert chosen.id == "paid/cheap"


def test_strict_schema_support_breaks_ties_within_a_price_tier():
    chosen = op.select_vision_model(
        _cat(_raw("free/loose", params=("response_format",), ctx=999999),
             _raw("free/strict", params=("structured_outputs", "response_format"), ctx=1000)),
        prefer=op.PREFER_FREE, today=TODAY)
    assert chosen.id == "free/strict", "a schema the provider enforces beats a bigger context"


def test_selection_is_deterministic():
    catalogue = _cat(_raw("b/model"), _raw("a/model"))
    picks = {op.select_vision_model(catalogue, prefer=op.PREFER_FREE, today=TODAY).id
             for _ in range(5)}
    assert picks == {"a/model"}


def test_no_usable_model_raises_rather_than_returning_something_unusable():
    with pytest.raises(VisionModelUnavailable, match="no OpenRouter model is usable"):
        op.select_vision_model(_cat(_raw("free/prose", params=("temperature",))),
                               prefer=op.PREFER_FREE, today=TODAY)


# ------------------------------------------------------------- resolution ----

def test_auto_resolves_from_the_catalogue(catalogue):
    model, why = op.resolve_model("auto", today=TODAY)
    assert model.id == "vendor/sees-images"
    assert "auto-selected" in why


def test_auto_free_and_auto_cheapest_are_both_accepted(catalogue):
    assert op.resolve_model("auto:free", today=TODAY)[0].is_free
    assert op.resolve_model("auto:cheapest", today=TODAY)[0].id in {
        "vendor/sees-images", "other/also-sees"}


def test_an_explicit_id_is_never_silently_overridden_by_auto_selection(catalogue):
    """Automatic selection is a convenience, not a policy the operator cannot escape."""
    model, why = op.resolve_model("other/also-sees", today=TODAY)
    assert model.id == "other/also-sees"
    assert "configured explicitly" in why


def test_an_explicit_id_that_cannot_return_json_is_refused(monkeypatch):
    monkeypatch.setattr(op, "_get_json", lambda url, **kw: {
        "data": [_raw("free/prose", params=("temperature",))]})
    with pytest.raises(VisionModelUnavailable, match="neither json_schema nor"):
        op.resolve_model("free/prose", today=TODAY)


@pytest.mark.asyncio
async def test_an_unconfigured_provider_still_refuses_to_guess(monkeypatch):
    """Auto-selection did not reintroduce a default. Unset is still unconfigured."""
    monkeypatch.delenv("PATHYAM_MOCK_VISION", raising=False)
    monkeypatch.delenv("PATHYAM_VISION_MODEL", raising=False)
    provider = OpenRouterVisionProvider(api_key=PLACEHOLDER_KEY)
    with pytest.raises(VisionNotConfigured, match="no vision model configured"):
        await provider.analyse_meal(FAKE_JPEG)


def test_the_free_tier_warning_names_the_actual_risk():
    """Free endpoints are free because the provider may use what you send, and what
    this provider sends is a photograph of someone's meal, usually at home."""
    warning = op.FREE_TIER_WARNING.lower()
    assert "train on or publish" in warning
    assert "personal data" in warning


# ------------------------------------------------------- request construction --

def test_the_request_carries_the_image_and_the_strict_schema(monkeypatch):
    """Pins the wire format: an image part, and a strict JSON-schema response format.

    The previous provider sent Google's schema dialect (uppercase "OBJECT",
    `nullable: true`), which an OpenAI-compatible endpoint does not accept.
    """
    captured: dict = {}

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self):
            return json.dumps({"choices": [{"message": {"content": '{"items": []}'}}]}).encode()

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["auth"] = req.headers.get("Authorization")
        captured["body"] = json.loads(req.data.decode())
        return _Resp()

    monkeypatch.setattr(op.urllib.request, "urlopen", fake_urlopen)

    provider = OpenRouterVisionProvider(api_key=PLACEHOLDER_KEY, model_name="vendor/sees-images")
    strict = _cat(_raw("vendor/sees-images"))[0]
    content, served_by = provider._post_completion(FAKE_JPEG, strict)
    assert content == '{"items": []}'

    assert captured["url"].endswith("/chat/completions")
    assert captured["auth"] == f"Bearer {PLACEHOLDER_KEY}"

    body = captured["body"]
    assert body["model"] == "vendor/sees-images"
    parts = body["messages"][1]["content"]
    image_parts = [p for p in parts if p["type"] == "image_url"]
    assert len(image_parts) == 1
    assert image_parts[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")

    fmt = body["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"]["type"] == "object", "standard JSON Schema, not Google's dialect"


def test_a_provider_error_inside_a_200_body_is_not_treated_as_a_reading(monkeypatch):
    """OpenRouter reports upstream failures in the body with HTTP 200.

    Reading `choices` without checking `error` would surface an empty meal as if the
    model had genuinely seen nothing on the plate.
    """
    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self):
            return json.dumps({"error": {"code": 402, "message": "insufficient credits"}}).encode()

    monkeypatch.setattr(op.urllib.request, "urlopen", lambda req, timeout=None: _Resp())

    provider = OpenRouterVisionProvider(api_key=PLACEHOLDER_KEY, model_name="vendor/sees-images")
    model = _cat(_raw("vendor/sees-images"))[0]
    with pytest.raises(VisionError, match="insufficient credits"):
        provider._post_completion(FAKE_JPEG, model)


def test_a_model_without_strict_schemas_gets_the_schema_in_the_prompt(monkeypatch):
    """Most free vision models support json_object but not json_schema.

    Sending them a json_schema request is an error, not a graceful downgrade, so the
    request adapts: the format drops to json_object and the schema travels in the
    system prompt, where it is at least stated. Our parser becomes the only check,
    which is why a malformed reply must raise rather than yield an empty meal.
    """
    captured: dict = {}

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self):
            return json.dumps({"choices": [{"message": {"content": '{"items": []}'}}]}).encode()

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data.decode())
        return _Resp()

    monkeypatch.setattr(op.urllib.request, "urlopen", fake_urlopen)

    loose = _cat(_raw("free/loose", params=("response_format",)))[0]
    assert loose.response_format_mode == "json_object"

    provider = OpenRouterVisionProvider(api_key=PLACEHOLDER_KEY, model_name="free/loose")
    provider._post_completion(FAKE_JPEG, loose)

    body = captured["body"]
    assert body["response_format"] == {"type": "json_object"}
    system = body["messages"][0]["content"]
    assert "visual_label" in system, "the schema must reach a model that cannot be given one"
    assert "estimated_portion" in system


def test_a_router_is_never_selected_automatically():
    """openrouter/free and openrouter/auto forward to some other model.

    Found by a real call: `openrouter/free` advertises structured_outputs and
    answered a meal photo with the bare string 'User Safety: safe' — because the
    capability belongs to the router, not to whichever model it picked. It
    "selects free models at random", so it also makes model_version name the router
    instead of what read the photograph, and lets two photos in one sitting be read
    by different models with nothing recording the switch.

    Explicit configuration can still name one; this governs `auto` only.
    """
    catalogue = _cat(_raw("openrouter/free", ctx=200000),
                     _raw("vendor/real-model", ctx=1000))
    assert [m.id for m in op.usable_models(catalogue, today=TODAY)] == ["vendor/real-model"]
    assert op.select_vision_model(
        catalogue, prefer=op.PREFER_FREE, today=TODAY).id == "vendor/real-model"


def test_an_explicitly_configured_router_is_still_allowed(monkeypatch):
    """The exclusion is about automatic selection, not about forbidding the model."""
    monkeypatch.setattr(op, "_get_json", lambda url, **kw: {
        "data": [_raw("openrouter/free"), _raw("vendor/real-model")]})
    model, why = op.resolve_model("openrouter/free", today=TODAY)
    assert model.id == "openrouter/free"
    assert "configured explicitly" in why


# ------------------------------------- provenance from the response (per docs) ----

def _stub_response(monkeypatch, payload: dict):
    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps(payload).encode()
    monkeypatch.setattr(op.urllib.request, "urlopen", lambda req, timeout=None: _Resp())


def test_the_model_that_answered_is_read_back_from_the_response(monkeypatch):
    """OpenRouter's response carries the model that actually served the request.

    It is not always the one asked for — a router forwards to a model chosen per
    request — so this is the only honest value for model_version. The free-router
    guide states the response reports which model was used.
    """
    _stub_response(monkeypatch, {
        "model": "some-vendor/actually-answered",
        "choices": [{"message": {"content": '{"items": []}'}}],
    })
    provider = OpenRouterVisionProvider(api_key=PLACEHOLDER_KEY, model_name="openrouter/free")
    asked = _cat(_raw("openrouter/free"))[0]

    _content, served_by = provider._post_completion(FAKE_JPEG, asked)
    assert served_by == "some-vendor/actually-answered", \
        "recording the router's own name would say nothing about what read the photo"


def test_the_asked_for_model_is_used_when_the_response_names_none(monkeypatch):
    _stub_response(monkeypatch, {"choices": [{"message": {"content": '{"items": []}'}}]})
    provider = OpenRouterVisionProvider(api_key=PLACEHOLDER_KEY, model_name="vendor/sees-images")
    asked = _cat(_raw("vendor/sees-images"))[0]
    assert provider._post_completion(FAKE_JPEG, asked)[1] == "vendor/sees-images"


def test_an_error_inside_a_choice_is_not_read_as_an_empty_meal(monkeypatch):
    """The API reference documents an ErrorResponse appearing within a choice.

    Checking only the top-level `error` would surface a provider failure as a meal
    with no items, as though the model had genuinely seen nothing on the plate.
    """
    _stub_response(monkeypatch, {
        "model": "vendor/sees-images",
        "choices": [{"error": {"code": 502, "message": "upstream provider failed"}}],
    })
    provider = OpenRouterVisionProvider(api_key=PLACEHOLDER_KEY, model_name="vendor/sees-images")
    asked = _cat(_raw("vendor/sees-images"))[0]
    with pytest.raises(VisionError, match="upstream provider failed"):
        provider._post_completion(FAKE_JPEG, asked)


def test_the_attribution_header_uses_the_documented_name(monkeypatch):
    captured: dict = {}

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self):
            return json.dumps({"choices": [{"message": {"content": '{"items": []}'}}]}).encode()

    def fake_urlopen(req, timeout=None):
        captured["headers"] = {k.lower(): v for k, v in req.headers.items()}
        return _Resp()

    monkeypatch.setattr(op.urllib.request, "urlopen", fake_urlopen)
    provider = OpenRouterVisionProvider(api_key=PLACEHOLDER_KEY, model_name="vendor/sees-images")
    provider._post_completion(FAKE_JPEG, _cat(_raw("vendor/sees-images"))[0])

    assert captured["headers"].get("X-openrouter-title".lower()) == "Pathyam"
    assert captured["headers"].get("Http-referer".lower())


# ------------------------------------------- compatible gateways (OmniRoute) ----
#
# The provider speaks OpenAI-compatible chat/completions, so any gateway serving
# that shape works — OmniRoute (self-hosted, localhost:20128/v1), LiteLLM, a proxy.
# What does NOT transfer is OpenRouter's catalogue, which is what auto-selection
# ranks on.


def test_a_gateway_base_url_overrides_openrouter(monkeypatch):
    monkeypatch.setenv("PATHYAM_VISION_BASE_URL", "http://localhost:20128/v1")
    assert op._base_url() == "http://localhost:20128/v1"
    assert not op.is_openrouter(op._base_url())


def test_auto_is_refused_off_openrouter(monkeypatch):
    """Selection ranks on OpenRouter's pricing, modality and capability fields.

    A compatible gateway's /models does not carry them, and ranking absent fields
    would pick badly in silence rather than fail. Requiring an explicit id is the
    honest outcome.
    """
    monkeypatch.setenv("PATHYAM_VISION_BASE_URL", "http://localhost:20128/v1")
    with pytest.raises(VisionNotConfigured, match="needs OpenRouter's catalogue"):
        op.resolve_model("auto:free", today=TODAY)


def test_an_explicit_id_on_a_gateway_is_trusted_and_assumes_the_weaker_json_mode(monkeypatch):
    """Absence from a gateway's catalogue proves nothing about the model.

    json_object is assumed rather than json_schema: asking for a strict schema where
    it is unsupported is an error, not a graceful downgrade.
    """
    monkeypatch.setenv("PATHYAM_VISION_BASE_URL", "http://localhost:20128/v1")
    monkeypatch.setattr(op, "_get_json", lambda url, **kw: {"data": []})

    model, why = op.resolve_model("some-provider/some-model", today=TODAY)
    assert model.id == "some-provider/some-model"
    assert model.response_format_mode == "json_object"
    assert "non-OpenRouter gateway" in why


def test_the_compression_hazard_is_stated_where_an_operator_will_meet_it():
    """A gateway that paraphrases the prompt in flight can drop the negations that
    separate perception from computation, and the client cannot detect it."""
    w = op.GATEWAY_COMPRESSION_WARNING.lower()
    assert "compression" in w
    assert "not calculate" in w or "do not calculate" in w
    assert "cannot be detected" in w


def test_a_development_gateway_is_refused_in_production(monkeypatch):
    """Dev-only means enforced, not documented.

    An env var surviving a promotion to production would silently route users' meal
    photographs through a gateway that can rewrite the prompt and fan out across
    upstream providers — defeating both provider_may_train_on_input and the
    vision_third_party consent it backs.
    """
    monkeypatch.setenv("PATHYAM_VISION_BASE_URL", "http://localhost:20128/v1")
    monkeypatch.setenv("PATHYAM_ENV", "production")
    with pytest.raises(VisionNotConfigured, match="development gateway"):
        op._base_url()


def test_pointing_at_openrouter_explicitly_is_fine_in_production(monkeypatch):
    """The guard is about gateways, not about overriding the URL."""
    monkeypatch.setenv("PATHYAM_VISION_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("PATHYAM_ENV", "production")
    assert op._base_url() == "https://openrouter.ai/api/v1"
