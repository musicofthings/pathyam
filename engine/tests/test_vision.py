"""Tests for Meal Photo Vision Engine & Gemini 3.7 Flash Adapter."""

from __future__ import annotations

import io
import pytest

from pathyam_engine.vision import (
    GeminiVisionProvider,
    ImageQualityGate,
    MealObservation,
    evaluate_image_quality,
)


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
async def test_gemini_vision_provider_structured_observation():
    provider = GeminiVisionProvider()
    fake_image = b"\xff\xd8\xff\xe0" + b"\x00" * 200

    obs: MealObservation = await provider.analyse_meal(fake_image)

    assert obs.model_version == "gemini-3.7-flash"
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
