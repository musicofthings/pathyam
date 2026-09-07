"""Clinical Claim Generator and Evidence Engine.

Flow:
  Computed Result + Context -> Hybrid Retrieval (FTS + pgvector) ->
  Structured Claim Generator -> Citation Validator (NCBI / Crossref) ->
  Explainable Result with verified citations (or suppressed citations).
"""

from __future__ import annotations

from typing import Any

from .citation_validator import CONTRADICTED, VERIFIED, CitationValidator
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
        docs = self.retriever.retrieve(query, limit=3)
        if not docs:
            # Fallback retrieve top guidelines
            docs = self.retriever.corpus[:2]

        doc_map = {d.evidence_id: d for d in docs}

        # 2. Build structured claims bound to retrieved evidence_ids
        claims: list[EvidenceClaim] = []
        suppressed_count = 0
        contradicted: list[str] = []

        for doc in docs:
            verified_citations: list[CitationVerificationResult] = []

            # Every check is given the claim to compare against. Passing only the
            # identifier asks "does this exist", which a fabricated citation passes.
            checks = []
            if doc.pmid:
                checks.append(self.validator.validate_pmid(
                    doc.pmid, claimed_title=doc.title, claimed_authors=doc.authors,
                    claimed_year=doc.publication_year))
            if doc.doi:
                checks.append(self.validator.validate_doi(
                    doc.doi, claimed_title=doc.title, claimed_authors=doc.authors,
                    claimed_year=doc.publication_year))
            if doc.guideline_ref:
                checks.append(self.validator.validate_guideline(doc.guideline_ref))

            for check in checks:
                if check.status == VERIFIED:
                    verified_citations.append(check)
                else:
                    suppressed_count += 1
                if check.status == CONTRADICTED:
                    # An identifier resolving to a different work is not a citation
                    # that failed to verify -- it is a claim with someone else's
                    # reference attached. Record it loudly.
                    contradicted.append(
                        f"{doc.evidence_id}: {check.failure_reason}"
                    )

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
            contradicted_citations=contradicted,
        )
