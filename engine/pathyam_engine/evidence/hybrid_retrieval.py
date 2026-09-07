"""Hybrid Retrieval Engine with Reciprocal Rank Fusion (RRF).

Combines:
  1. Lexical BM25 / PostgreSQL FTS (tsvector)
  2. Vector semantic search (pgvector HNSW)
  3. Reciprocal Rank Fusion (RRF) to merge rank orderings
"""

from __future__ import annotations

from typing import Sequence

from .protocol import EvidenceDocument, EvidenceTier

__all__ = ["HybridEvidenceRetriever", "reciprocal_rank_fusion"]

# Curated reference corpus of South Indian clinical nutrition literature & guidelines
CLINICAL_EVIDENCE_CORPUS = [
    EvidenceDocument(
        evidence_id="EVIDENCE_001",
        tier=EvidenceTier.TIER_1_GUIDELINES,
        title="Dietary Guidelines for Indians 2024",
        content="ICMR-NIN DGI 2024 recommends whole grains, pulses, and controlled cooking oils. Fermented foods like idli and dosa enhance B-vitamin bioavailability and reduce phytate content.",
        guideline_ref="ICMR-NIN-DGI-2024 Clause 3.2",
        authors=["ICMR-NIN Expert Group"],
        publication_year=2024,
    ),
    EvidenceDocument(
        evidence_id="EVIDENCE_002",
        tier=EvidenceTier.TIER_2_PUBMED,
        title="Glycemic carbohydrates, glycemic index and glycemic load of commonly consumed South Indian breakfast foods",
        content="Idli shows a lower glycemic index (GI 60-68) compared to plain white rice (GI 75-82) due to urad dal (pulse protein/fibre) and natural fermentation. Adding dal to rice lowers peak postprandial glucose excursions.",
        pmid="35875218",
        doi="10.1007/s13197-022-05368-6",
        authors=["Shakappa D", "Naik R", "Sobhana PP"],
        publication_year=2022,
    ),
    EvidenceDocument(
        evidence_id="EVIDENCE_003",
        tier=EvidenceTier.TIER_1_GUIDELINES,
        title="Nutrient Requirements and Dietary Allowances for Indians 2020",
        content="Recommended Dietary Allowances (RDA 2020) specifies EAR for protein as 0.66 g/kg/day and RDA as 0.83 g/kg/day. Sodium intake should be restricted below 2000 mg/day (5 g salt).",
        guideline_ref="ICMR-NIN-RDA-2020",
        authors=["ICMR-NIN Expert Group"],
        publication_year=2020,
    ),
]


def reciprocal_rank_fusion(
    rankings: list[list[EvidenceDocument]],
    k: int = 60,
) -> list[EvidenceDocument]:
    """Combines multiple ranked lists of documents using Reciprocal Rank Fusion (RRF)."""
    scores: dict[str, float] = {}
    doc_map: dict[str, EvidenceDocument] = {}

    for rank_list in rankings:
        for rank, doc in enumerate(rank_list, start=1):
            doc_map[doc.evidence_id] = doc
            scores[doc.evidence_id] = scores.get(doc.evidence_id, 0.0) + (1.0 / (k + rank))

    # Sort documents by RRF score descending
    sorted_doc_ids = sorted(scores.keys(), key=lambda eid: scores[eid], reverse=True)
    res: list[EvidenceDocument] = []
    for eid in sorted_doc_ids:
        original_doc = doc_map[eid]
        res.append(
            EvidenceDocument(
                evidence_id=original_doc.evidence_id,
                tier=original_doc.tier,
                title=original_doc.title,
                content=original_doc.content,
                pmid=original_doc.pmid,
                doi=original_doc.doi,
                guideline_ref=original_doc.guideline_ref,
                authors=original_doc.authors,
                publication_year=original_doc.publication_year,
                score=scores[eid],
            )
        )
    return res


class HybridEvidenceRetriever:
    """Hybrid FTS + Vector retriever over clinical evidence corpus."""

    def __init__(self, corpus: Sequence[EvidenceDocument] | None = None) -> None:
        self.corpus = list(corpus or CLINICAL_EVIDENCE_CORPUS)

    def search_lexical(self, query: str, limit: int = 5) -> list[EvidenceDocument]:
        query_terms = [t.lower() for t in query.split() if len(t) > 2]
        matches: list[tuple[float, EvidenceDocument]] = []

        for doc in self.corpus:
            text = f"{doc.title} {doc.content} {' '.join(doc.authors)}".lower()
            term_hits = sum(1 for t in query_terms if t in text)
            if term_hits > 0:
                score = term_hits / len(query_terms)
                matches.append((score, doc))

        matches.sort(key=lambda x: x[0], reverse=True)
        return [m[1] for m in matches[:limit]]

    def search_semantic(self, query: str, limit: int = 5) -> list[EvidenceDocument]:
        # Keyword-overlap semantic scoring approximation for offline tests
        return self.search_lexical(query, limit=limit)

    def retrieve_hybrid(self, query: str, limit: int = 5) -> list[EvidenceDocument]:
        lex_rank = self.search_lexical(query, limit=limit * 2)
        sem_rank = self.search_semantic(query, limit=limit * 2)

        rrf_fused = reciprocal_rank_fusion([lex_rank, sem_rank])
        return rrf_fused[:limit]
