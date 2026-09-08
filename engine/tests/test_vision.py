"""Vision provider: configuration, failure behaviour, and the mock's labelling.

Every test here pins a property that a previous version of this module violated.
No test in this file makes a network call.
"""

from __future__ import annotations

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

    provider = OpenRouterVisionProvider(api_key=PLACEHOLDER_KEY, model_name="some/model")

    def boom(_image):
        raise RuntimeError("upstream 404: model not found")

    monkeypatch.setattr(provider, "_post_completion", boom)

    with pytest.raises(VisionError) as excinfo:
        await provider.analyse_meal(FAKE_JPEG)
    assert "some/model" in str(excinfo.value), "the error must name the model tried"


@pytest.mark.asyncio
async def test_a_non_json_reply_raises_rather_than_yielding_an_empty_meal(monkeypatch):
    """json.loads used to run unguarded; a prose reply would have thrown a bare
    JSONDecodeError, and an empty dict would have produced a meal with no items."""
    monkeypatch.delenv("PATHYAM_MOCK_VISION", raising=False)
    provider = OpenRouterVisionProvider(api_key=PLACEHOLDER_KEY, model_name="some/model")
    monkeypatch.setattr(provider, "_post_completion",
                        lambda _i: "I'm sorry, I can't identify this photo.")

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
# public catalogue before a key exists. These tests stub the catalogue fetch.

_CATALOGUE = {
    "data": [
        {"id": "vendor/sees-images", "architecture": {"input_modalities": ["text", "image"]}},
        {"id": "vendor/text-only", "architecture": {"input_modalities": ["text"]}},
        {"id": "other/also-sees", "architecture": {"input_modalities": ["text", "image"]}},
    ]
}


@pytest.fixture
def catalogue(monkeypatch):
    monkeypatch.setattr(op, "_get_json", lambda url, **kw: _CATALOGUE)


def test_verify_accepts_a_model_that_exists_and_takes_images(catalogue):
    verify_model_id("vendor/sees-images")     # does not raise


def test_verify_rejects_a_model_id_that_does_not_exist(catalogue):
    """The exact bug that shipped: 'gemini-3.7-flash' was never a real model."""
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
    assert provider._post_completion(FAKE_JPEG) == '{"items": []}'

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
    with pytest.raises(VisionError, match="insufficient credits"):
        provider._post_completion(FAKE_JPEG)
