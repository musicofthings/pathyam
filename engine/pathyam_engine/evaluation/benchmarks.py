"""Safety and accuracy benchmarks over a golden meal dataset.

Every field on :class:`BenchmarkMetrics` is computed from the inputs, or is ``None``.
Nothing here returns a plausible-looking constant when it has nothing to measure --
an earlier version of this module hardcoded citation recall at 0.92 and latency at
145 ms regardless of input, and returned perfect precision and recall for an empty
sample set. A safety benchmark that invents its own numbers is worse than no
benchmark, because it is quoted as though it means something.

``None`` means "not measured". Callers must render it as such, not as zero.

Covers:
  1. Food resolution accuracy (top-1 / top-3)
  2. Portion MAPE against weighed records
  3. Nutrient error % against weighed nutrient totals
  4. Citation precision and recall against the sample's known-relevant PMIDs
  5. Unsupported claim rate (claims carrying no citation at all)
  6. Latency, when predictions carry a measured `latency_ms`

NOTE: as of this writing no golden meal dataset exists in the repository -- the only
`GoldenMealSample` instances are two synthetic rows in the unit test. Until real
weighed meals are collected, this suite has nothing to run against and its output
must not be quoted as a measurement of the system.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

__all__ = [
    "GoldenMealSample",
    "BenchmarkMetrics",
    "SafetyBenchmarkSuite",
]


@dataclass(frozen=True)
class GoldenMealSample:
    sample_id: str
    image_ref: str
    ground_truth_foods: list[str]                # Canonical food_ids
    ground_truth_portion_g: list[float]          # Weighed record masses
    ground_truth_kcal: float                     # Weighed nutrient total
    ground_truth_pmids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BenchmarkMetrics:
    """Measured benchmark results. ``None`` means the input carried nothing to measure."""

    food_resolution_top1: float | None
    food_resolution_top3: float | None
    portion_mape: float | None                   # Fraction, e.g. 0.21 = 21%
    nutrient_error_pct: float | None             # Mean absolute deviation, percent
    citation_precision: float | None             # Emitted citations that validated
    citation_recall: float | None                # Known-relevant citations retrieved
    unsupported_claim_rate: float | None         # Claims carrying no citation
    mean_latency_ms: float | None                # Only if predictions reported latency
    n_samples: int = 0
    model_version: str | None = None             # Whatever actually produced the run
    prompt_hash: str | None = None               # Real digest, or None

    def as_dict(self) -> dict[str, Any]:
        def pct(v: float | None) -> float | None:
            return None if v is None else round(v * 100.0, 2)

        return {
            "n_samples": self.n_samples,
            "food_resolution_top1": pct(self.food_resolution_top1),
            "food_resolution_top3": pct(self.food_resolution_top3),
            "portion_mape": pct(self.portion_mape),
            "nutrient_error_pct": (
                None if self.nutrient_error_pct is None else round(self.nutrient_error_pct, 2)
            ),
            "citation_precision": pct(self.citation_precision),
            "citation_recall": pct(self.citation_recall),
            "unsupported_claim_rate": pct(self.unsupported_claim_rate),
            "mean_latency_ms": (
                None if self.mean_latency_ms is None else round(self.mean_latency_ms, 1)
            ),
            "model_version": self.model_version,
            "prompt_hash": self.prompt_hash,
        }


def _mean(values: Sequence[float]) -> float | None:
    return (sum(values) / len(values)) if values else None


class SafetyBenchmarkSuite:
    """Evaluates predictions against a golden meal dataset."""

    def evaluate_samples(
        self,
        samples: Sequence[GoldenMealSample],
        predictions: Sequence[dict[str, Any]],
        *,
        model_version: str | None = None,
        prompt_hash: str | None = None,
    ) -> BenchmarkMetrics:
        if not samples:
            # Nothing measured. Every rate is None, not 1.0.
            return BenchmarkMetrics(
                food_resolution_top1=None,
                food_resolution_top3=None,
                portion_mape=None,
                nutrient_error_pct=None,
                citation_precision=None,
                citation_recall=None,
                unsupported_claim_rate=None,
                mean_latency_ms=None,
                n_samples=0,
                model_version=model_version,
                prompt_hash=prompt_hash,
            )

        top1_hits = 0
        top3_hits = 0
        portion_errors: list[float] = []
        kcal_errors: list[float] = []
        latencies: list[float] = []

        verified_citations = 0
        total_citations = 0
        relevant_retrieved = 0
        relevant_total = 0
        unsupported_claims = 0
        total_claims = 0

        for sample, pred in zip(samples, predictions):
            pred_foods = pred.get("predicted_foods", [])
            pred_portions = pred.get("predicted_portions_g", [])
            pred_kcal = pred.get("predicted_kcal")
            claims = pred.get("claims", [])

            if pred_foods and pred_foods[0] in sample.ground_truth_foods:
                top1_hits += 1
            if any(f in sample.ground_truth_foods for f in pred_foods[:3]):
                top3_hits += 1

            for gt_p, pr_p in zip(sample.ground_truth_portion_g, pred_portions):
                if gt_p > 0:
                    portion_errors.append(abs(pr_p - gt_p) / gt_p)

            if sample.ground_truth_kcal > 0 and pred_kcal is not None:
                kcal_errors.append(
                    abs(pred_kcal - sample.ground_truth_kcal) / sample.ground_truth_kcal * 100.0
                )

            latency = pred.get("latency_ms")
            if latency is not None:
                latencies.append(float(latency))

            # Citations. Precision is over what was emitted; recall is over the
            # PMIDs this sample is known to be supported by.
            emitted_valid_pmids: set[str] = set()
            for c in claims:
                total_claims += 1
                cits = c.get("citations", [])
                if not cits:
                    unsupported_claims += 1
                for cit in cits:
                    total_citations += 1
                    if cit.get("is_valid"):
                        verified_citations += 1
                        pmid = cit.get("pmid")
                        if pmid:
                            emitted_valid_pmids.add(str(pmid))

            if sample.ground_truth_pmids:
                expected = {str(p) for p in sample.ground_truth_pmids}
                relevant_total += len(expected)
                relevant_retrieved += len(expected & emitted_valid_pmids)

        n = len(samples)
        return BenchmarkMetrics(
            food_resolution_top1=top1_hits / n,
            food_resolution_top3=top3_hits / n,
            portion_mape=_mean(portion_errors),
            nutrient_error_pct=_mean(kcal_errors),
            citation_precision=(verified_citations / total_citations) if total_citations else None,
            citation_recall=(relevant_retrieved / relevant_total) if relevant_total else None,
            unsupported_claim_rate=(unsupported_claims / total_claims) if total_claims else None,
            mean_latency_ms=_mean(latencies),
            n_samples=n,
            model_version=model_version,
            prompt_hash=prompt_hash,
        )
