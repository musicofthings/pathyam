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


def test_a_registered_guideline_still_verifies_offline():
    """The only check that needs no network.

    This replaces a test that asserted validate_pmid("35875218") with no claimed
    title was valid. Under the current contract that is UNVERIFIED, deliberately:
    a resolving identifier proves existence, not that it supports the claim. The
    PMID and DOI paths are covered against stubbed registries in
    tests/test_citation_validation.py, which also does not touch the network.
    """
    result = CitationValidator().validate_guideline("ICMR-NIN-DGI-2024")
    assert result.is_valid
    assert not result.suppressed


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


def test_retriever_ranks_and_scores_the_corpus():
    retriever = HybridEvidenceRetriever()
    results = retriever.retrieve("idli glycemic index fermentation", limit=2)

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
