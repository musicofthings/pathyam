"""Vision provider: configuration, failure behaviour, and the mock's labelling."""

from __future__ import annotations

import io
import pytest

from pathyam_engine.vision import (
    MOCK_MODEL_VERSION,
    GeminiVisionProvider,
    ImageQualityGate,
    MealObservation,
    VisionError,
    VisionNotConfigured,
    evaluate_image_quality,
)

FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 200


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
    provider = GeminiVisionProvider(api_key="unused", model_name="unused")

    obs: MealObservation = await provider.analyse_meal(FAKE_JPEG)

    assert obs.model_version == MOCK_MODEL_VERSION == "mock"
    assert len(obs.items) >= 1

    first_item = obs.items[0]
    assert first_item.visual_label
    assert first_item.estimated_portion.grams is not None or first_item.estimated_portion.millilitres is not None
    assert first_item.estimated_portion.uncertainty in ("low", "medium", "high")
    assert first_item.confidence > 0.0

    # Ensure VLM response carries NO calorie or nutrient numbers directly
    obs_dict = obs.as_dict()
    assert "calories" not in str(obs_dict).lower()
    assert "kcal" not in str(obs_dict).lower()


@pytest.mark.asyncio
async def test_a_missing_api_key_raises_instead_of_returning_a_mock(monkeypatch):
    """Regression: this used to return a hardcoded plate of dosa and sambar."""
    monkeypatch.delenv("PATHYAM_MOCK_VISION", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    with pytest.raises(VisionNotConfigured, match="no Gemini API key"):
        await GeminiVisionProvider().analyse_meal(FAKE_JPEG)


@pytest.mark.asyncio
async def test_a_missing_model_id_raises_rather_than_defaulting(monkeypatch):
    """There is deliberately no default: the old one was not a real model."""
    monkeypatch.delenv("PATHYAM_MOCK_VISION", raising=False)
    monkeypatch.delenv("PATHYAM_VISION_MODEL", raising=False)

    provider = GeminiVisionProvider(api_key="present-but-no-model")
    with pytest.raises(VisionNotConfigured, match="no vision model configured"):
        await provider.analyse_meal(FAKE_JPEG)


@pytest.mark.asyncio
async def test_an_api_failure_raises_and_never_degrades_into_the_mock(monkeypatch):
    """The failure mode that made every meal photo return dosa and sambar."""
    monkeypatch.delenv("PATHYAM_MOCK_VISION", raising=False)

    provider = GeminiVisionProvider(api_key="k", model_name="some-model")

    async def boom(_image):
        raise RuntimeError("upstream 404: model not found")

    monkeypatch.setattr(provider, "_call_gemini_api", boom)

    with pytest.raises(VisionError) as excinfo:
        await provider.analyse_meal(FAKE_JPEG)
    assert "some-model" in str(excinfo.value), "the error must name the model tried"


def test_no_default_model_id_is_baked_into_the_provider(monkeypatch):
    """Guard against a fabricated default creeping back in.

    The model id must come from configuration. An unconfigured provider carries
    None and fails loudly at call time, rather than carrying a plausible-looking
    string that 404s on every request.
    """
    monkeypatch.delenv("PATHYAM_VISION_MODEL", raising=False)
    assert GeminiVisionProvider(api_key="k").model_name is None

    monkeypatch.setenv("PATHYAM_VISION_MODEL", "configured-by-operator")
    assert GeminiVisionProvider(api_key="k").model_name == "configured-by-operator"
