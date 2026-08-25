"""Tests for Evidence Engine & Citation Hallucination Prevention."""

from __future__ import annotations

import pytest

from pathyam_engine.evidence import (
    CitationValidator,
    EvidenceEngine,
    EvidenceTier,
    HybridEvidenceRetriever,
    reciprocal_rank_fusion,
    validate_citation,
)


def test_citation_validator_validates_known_pmid_and_doi():
    validator = CitationValidator()

    # Valid PMID
    res_pmid = validator.validate_pmid("35875218")
    assert res_pmid.is_valid
    assert not res_pmid.suppressed
    assert "South Indian" in str(res_pmid.title_match)

    # Valid DOI
    res_doi = validator.validate_doi("10.1007/s13197-022-05368-6")
    assert res_doi.is_valid
    assert not res_doi.suppressed

    # Valid Guideline
    res_guide = validator.validate_guideline("ICMR-NIN-DGI-2024")
    assert res_guide.is_valid
    assert not res_guide.suppressed


def test_citation_validator_suppresses_fake_pmid_and_doi():
    validator = CitationValidator()

    # Fake PMID
    res_fake_pmid = validator.validate_pmid("9999999999")
    assert not res_fake_pmid.is_valid
    assert res_fake_pmid.suppressed
    assert res_fake_pmid.failure_reason is not None

    # Fake DOI
    res_fake_doi = validator.validate_doi("10.99999/fake-doi-12345")
    assert not res_fake_doi.is_valid
    assert res_fake_doi.suppressed


def test_hybrid_retriever_reciprocal_rank_fusion():
    retriever = HybridEvidenceRetriever()
    results = retriever.retrieve_hybrid("idli glycemic index fermentation", limit=2)

    assert len(results) >= 1
    assert any("35875218" in str(r.pmid) or "2024" in str(r.title) for r in results)
    assert results[0].score > 0.0


def test_evidence_engine_generates_explanation_with_verified_citations():
    engine = EvidenceEngine()
    exp = engine.generate_explanation("Why does idli have a lower glycemic index than plain rice?")

    assert exp.clinical_explanation
    assert len(exp.claims) >= 1
    assert any(c.verified_citations for c in exp.claims)
    assert exp.suppressed_citations_count == 0
