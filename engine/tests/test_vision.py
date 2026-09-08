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
                        lambda _i, _m: "I'm sorry, I can't identify this photo.")

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
    assert provider._post_completion(FAKE_JPEG, strict) == '{"items": []}'

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
