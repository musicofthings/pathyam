"""Evidence Engine Protocol and Dataclasses.

Implements the clinical evidence layer:
Computed Result + Clinical Context -> Evidence Query Builder -> Curated Evidence Corpus ->
Hybrid Retrieval (pgvector + FTS) -> Reciprocal Rank Fusion -> Claim Generator ->
Citation Validator -> Clinical Explanation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "EvidenceTier",
    "EvidenceDocument",
    "EvidenceClaim",
    "CitationVerificationResult",
    "ExplanationResult",
]


class EvidenceTier:
    TIER_1_GUIDELINES = "TIER_1_GUIDELINES"  # ICMR-NIN DGI 2024, RDA 2020, WHO
    TIER_2_PUBMED = "TIER_2_PUBMED"          # PubMed indexed literature via NCBI E-utilities
    TIER_3_PMC = "TIER_3_PMC"                # PMC Open-Access full text
    TIER_4_CROSSREF = "TIER_4_CROSSREF"      # Crossref DOI metadata


@dataclass(frozen=True)
class EvidenceDocument:
    evidence_id: str                          # e.g. 'EVIDENCE_001'
    tier: str                                 # EvidenceTier
    title: str
    content: str
    pmid: str | None = None
    doi: str | None = None
    guideline_ref: str | None = None          # e.g. 'ICMR-NIN-DGI-2024 Clause 3.2'
    authors: list[str] = field(default_factory=list)
    publication_year: int | None = None
    score: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "tier": self.tier,
            "title": self.title,
            "content": self.content,
            "pmid": self.pmid,
            "doi": self.doi,
            "guideline_ref": self.guideline_ref,
            "authors": self.authors,
            "publication_year": self.publication_year,
            "score": round(self.score, 4),
        }


@dataclass(frozen=True)
class CitationVerificationResult:
    is_valid: bool
    pmid: str | None = None
    doi: str | None = None
    verification_source: str = ""             # 'ncbi', 'crossref', 'guideline_registry'
    title_match: str | None = None
    suppressed: bool = False
    failure_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "pmid": self.pmid,
            "doi": self.doi,
            "verification_source": self.verification_source,
            "title_match": self.title_match,
            "suppressed": self.suppressed,
            "failure_reason": self.failure_reason,
        }


@dataclass(frozen=True)
class EvidenceClaim:
    statement: str
    evidence_ids: list[str]
    certainty: str = "high"                  # 'high' | 'moderate' | 'low'
    verified_citations: list[CitationVerificationResult] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "statement": self.statement,
            "evidence_ids": self.evidence_ids,
            "certainty": self.certainty,
            "verified_citations": [c.as_dict() for c in self.verified_citations],
        }


@dataclass(frozen=True)
class ExplanationResult:
    clinical_explanation: str
    claims: list[EvidenceClaim]
    retrieved_documents: list[EvidenceDocument]
    suppressed_citations_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "clinical_explanation": self.clinical_explanation,
            "claims": [c.as_dict() for c in self.claims],
            "retrieved_documents": [d.as_dict() for d in self.retrieved_documents],
            "suppressed_citations_count": self.suppressed_citations_count,
        }
