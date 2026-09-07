"""Evidence retrieval over the curated corpus.

WHAT THIS ACTUALLY IS, stated plainly because the previous docstring did not.

It is a **keyword matcher** over a small in-memory corpus: term overlap against each
document's title, body and authors. That is all. There is no BM25, no PostgreSQL
full-text search, and no pgvector.

The previous docstring advertised "Lexical BM25 / PostgreSQL FTS (tsvector)" and
"Vector semantic search (pgvector HNSW)" fused by reciprocal rank. In fact
``search_semantic`` returned ``search_lexical`` unchanged, so the fusion combined two
identical rankings and could not reorder anything -- an expensive no-op wearing the
name of a technique. That description is what the phrase "Evidence engine" in the
release notes was resting on.

:func:`reciprocal_rank_fusion` is correct and kept. It has nothing to fuse yet, and
becomes useful the moment a genuinely different retrieval arm exists.

TO MAKE THIS REAL
-----------------
The corpus needs to live in Postgres rather than in this file, with a ``tsvector``
column for lexical search and an embedding column for semantic search. The second
needs an embedding provider, which this repository has no credentials for -- so
adding a ``search_semantic`` that quietly returns keyword results again would be
worse than having none. Until then there is one arm, and it is named for what it does.
"""

from __future__ import annotations

from dataclasses import replace
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
        # Attach the score to the returned documents. Callers rank on it, and
        # previously it was left at the dataclass default of 0.0 unless the value
        # happened to pass through reciprocal_rank_fusion.
        return [
            replace(doc, score=score) for score, doc in matches[:limit]
        ]

    def retrieve(self, query: str, limit: int = 5) -> list[EvidenceDocument]:
        """Rank the corpus for a query. One arm, so no fusion is performed.

        Running :func:`reciprocal_rank_fusion` over a single ranking would only
        rewrite the scores into RRF units while preserving the order, which reads
        like fusion is happening. It is not.
        """
        return self.search_lexical(query, limit=limit)

    # Kept so existing callers do not break. Deliberately not named "hybrid": there
    # is one retrieval arm, and calling it hybrid is how the previous version came to
    # describe a keyword matcher as pgvector + BM25.
    retrieve_hybrid = retrieve
