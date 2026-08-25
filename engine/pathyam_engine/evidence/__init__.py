"""Pathyam Evidence Engine Package."""

from .citation_validator import CitationValidator, validate_citation
from .claim_generator import EvidenceEngine
from .crossref_client import CrossrefClient
from .hybrid_retrieval import HybridEvidenceRetriever, reciprocal_rank_fusion
from .ncbi_client import NCBIClient
from .protocol import (
    CitationVerificationResult,
    EvidenceClaim,
    EvidenceDocument,
    EvidenceTier,
    ExplanationResult,
)

__all__ = [
    "EvidenceTier",
    "EvidenceDocument",
    "CitationVerificationResult",
    "EvidenceClaim",
    "ExplanationResult",
    "NCBIClient",
    "CrossrefClient",
    "CitationValidator",
    "validate_citation",
    "HybridEvidenceRetriever",
    "reciprocal_rank_fusion",
    "EvidenceEngine",
]
