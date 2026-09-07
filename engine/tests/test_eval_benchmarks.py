"""Tests for Evaluation, Safety & Observability Benchmarks (5th Layer)."""

from __future__ import annotations

import pytest

from pathyam_engine.evaluation import (
    BenchmarkMetrics,
    GoldenMealSample,
    SafetyBenchmarkSuite,
)


def test_safety_benchmark_suite_computes_accuracy_mape_and_citation_metrics():
    suite = SafetyBenchmarkSuite()

    samples = [
        GoldenMealSample(
            sample_id="SAMPLE_001",
            image_ref="dosa_photo_01.jpg",
            ground_truth_foods=["PY-F-000100", "PY-F-000103"],
            ground_truth_portion_g=[120.0, 140.0],
            ground_truth_kcal=310.0,
            ground_truth_pmids=["35875218"],
        ),
        GoldenMealSample(
            sample_id="SAMPLE_002",
            image_ref="idli_photo_02.jpg",
            ground_truth_foods=["PY-F-000110"],
            ground_truth_portion_g=[180.0],
            ground_truth_kcal=215.0,
            ground_truth_pmids=["35875218"],
        ),
    ]

    predictions = [
        {
            "predicted_foods": ["PY-F-000100", "PY-F-000103"],
            "predicted_portions_g": [120.0, 140.0],
            "predicted_kcal": 315.0,
            "claims": [
                {"citations": [{"is_valid": True, "pmid": "35875218"}]}
            ],
        },
        {
            "predicted_foods": ["PY-F-000110"],
            "predicted_portions_g": [180.0],
            "predicted_kcal": 215.0,
            "claims": [
                {"citations": [{"is_valid": True, "pmid": "35875218"}]}
            ],
        },
    ]

    metrics = suite.evaluate_samples(samples, predictions, model_version="test-provider")

    assert metrics.food_resolution_top1 == 1.0
    assert metrics.food_resolution_top3 == 1.0
    assert metrics.portion_mape == 0.0
    assert metrics.citation_precision == 1.0
    assert metrics.citation_recall == 1.0
    assert metrics.unsupported_claim_rate == 0.0
    assert metrics.n_samples == 2

    dict_repr = metrics.as_dict()
    assert dict_repr["food_resolution_top1"] == 100.0
    assert dict_repr["portion_mape"] == 0.0
    assert dict_repr["model_version"] == "test-provider"
    # No prediction reported a latency, so latency is unmeasured -- not zero.
    assert dict_repr["mean_latency_ms"] is None


def test_citation_recall_falls_when_a_known_relevant_pmid_is_not_retrieved():
    suite = SafetyBenchmarkSuite()
    samples = [
        GoldenMealSample(
            sample_id="SAMPLE_003",
            image_ref="pongal_photo_03.jpg",
            ground_truth_foods=["PY-F-000120"],
            ground_truth_portion_g=[200.0],
            ground_truth_kcal=280.0,
            ground_truth_pmids=["35875218", "31234568"],
        ),
    ]
    predictions = [
        {
            "predicted_foods": ["PY-F-000120"],
            "predicted_portions_g": [200.0],
            "predicted_kcal": 280.0,
            "claims": [{"citations": [{"is_valid": True, "pmid": "35875218"}]}],
        },
    ]

    metrics = suite.evaluate_samples(samples, predictions)
    assert metrics.citation_precision == 1.0
    assert metrics.citation_recall == 0.5


def test_empty_sample_set_measures_nothing_rather_than_scoring_perfectly():
    """Regression guard: this used to return precision and recall of 1.0."""
    metrics = SafetyBenchmarkSuite().evaluate_samples([], [])

    assert metrics.n_samples == 0
    assert metrics.citation_precision is None
    assert metrics.citation_recall is None
    assert metrics.food_resolution_top1 is None
    assert metrics.unsupported_claim_rate is None
    assert metrics.mean_latency_ms is None
    assert metrics.prompt_hash is None
