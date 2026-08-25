"""Clinical Claim Generator and Evidence Engine.

Flow:
  Computed Result + Context -> Hybrid Retrieval (FTS + pgvector) ->
  Structured Claim Generator -> Citation Validator (NCBI / Crossref) ->
  Explainable Result with verified citations (or suppressed citations).
"""

from __future__ import annotations

from typing import Any

from .citation_validator import CitationValidator
from .hybrid_retrieval import HybridEvidenceRetriever
from .protocol import (
    CitationVerificationResult,
    EvidenceClaim,
    EvidenceDocument,
    ExplanationResult,
)

__all__ = ["EvidenceEngine"]


class EvidenceEngine:
    """Clinical Evidence Engine."""

    def __init__(
        self,
        retriever: HybridEvidenceRetriever | None = None,
        validator: CitationValidator | None = None,
    ) -> None:
        self.retriever = retriever or HybridEvidenceRetriever()
        self.validator = validator or CitationValidator()

    def generate_explanation(
        self,
        query: str,
        computed_context: dict[str, Any] | None = None,
    ) -> ExplanationResult:
        # 1. Retrieve evidence documents using Hybrid Retrieval (FTS + pgvector RRF)
        docs = self.retriever.retrieve_hybrid(query, limit=3)
        if not docs:
            # Fallback retrieve top guidelines
            docs = self.retriever.corpus[:2]

        doc_map = {d.evidence_id: d for d in docs}

        # 2. Build structured claims bound to retrieved evidence_ids
        claims: list[EvidenceClaim] = []
        suppressed_count = 0

        for doc in docs:
            verified_citations: list[CitationVerificationResult] = []

            # Check PMID validation
            if doc.pmid:
                ver_pmid = self.validator.validate_pmid(doc.pmid)
                if ver_pmid.is_valid:
                    verified_citations.append(ver_pmid)
                else:
                    suppressed_count += 1

            # Check DOI validation
            if doc.doi:
                ver_doi = self.validator.validate_doi(doc.doi)
                if ver_doi.is_valid:
                    verified_citations.append(ver_doi)
                else:
                    suppressed_count += 1

            # Check Guideline validation
            if doc.guideline_ref:
                ver_guide = self.validator.validate_guideline(doc.guideline_ref)
                if ver_guide.is_valid:
                    verified_citations.append(ver_guide)
                else:
                    suppressed_count += 1

            # Generate claim statement
            statement = f"{doc.title}: {doc.content}"
            claims.append(
                EvidenceClaim(
                    statement=statement,
                    evidence_ids=[doc.evidence_id],
                    certainty="high" if verified_citations else "moderate",
                    verified_citations=verified_citations,
                )
            )

        explanation_text = (
            f"Based on computed composition data and authoritative clinical evidence: "
            + " ".join(c.statement for c in claims[:2])
        )

        return ExplanationResult(
            clinical_explanation=explanation_text,
            claims=claims,
            retrieved_documents=docs,
            suppressed_citations_count=suppressed_count,
        )
