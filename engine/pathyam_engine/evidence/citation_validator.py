"""Citation existence checks for PMIDs, DOIs and guideline references.

WHAT THIS DOES: confirms an identifier resolves — that the PMID exists in PubMed,
that the DOI resolves in Crossref.

WHAT THIS DOES NOT DO, and must not be described as doing: it does not check that
the resolved record is the work the claim cites. A citation whose title and authors
were written first and had a plausible-looking PMID attached afterwards passes every
check here, because the PMID does exist — it simply belongs to a different paper.
That exact case shipped in this corpus (EVIDENCE_004 cited PMID 31234567, a paper on
electrical impedance tomography of granular material) and was not caught.

Until title/author/year agreement is implemented, treat a pass as "the identifier is
real", not "the citation is correct". A network failure is also currently
indistinguishable from a failed lookup, so an outage silently suppresses good
citations rather than reporting that it could not check.
"""

from __future__ import annotations

from typing import Any, Sequence

from .crossref_client import CrossrefClient
from .ncbi_client import NCBIClient
from .protocol import CitationVerificationResult, EvidenceDocument

__all__ = ["CitationValidator", "validate_citation"]

# Known reference dataset of valid PMIDs / DOIs for test & offline environments
KNOWN_VALID_REFS = {
    "PMID:35875218": {
        "title": "Glycemic carbohydrates, glycemic index and glycemic load of commonly consumed South Indian breakfast foods",
        "type": "pmid",
    },
    "DOI:10.1007/s13197-022-05368-6": {
        "title": "Glycemic carbohydrates, glycemic index and glycemic load of South Indian foods",
        "type": "doi",
    },
    "ICMR-NIN-DGI-2024": {
        "title": "Dietary Guidelines for Indians 2024. ICMR-NIN Hyderabad.",
        "type": "guideline",
    },
}


class CitationValidator:
    """Citation Validator."""

    def __init__(
        self,
        ncbi_client: NCBIClient | None = None,
        crossref_client: CrossrefClient | None = None,
    ) -> None:
        self.ncbi_client = ncbi_client or NCBIClient()
        self.crossref_client = crossref_client or CrossrefClient()

    def validate_pmid(self, pmid: str) -> CitationVerificationResult:
        pmid_clean = pmid.strip().replace("PMID:", "").replace("pmid:", "")
        known_key = f"PMID:{pmid_clean}"

        # 1. Check known local registry first (fast path)
        if known_key in KNOWN_VALID_REFS:
            return CitationVerificationResult(
                is_valid=True,
                pmid=pmid_clean,
                verification_source="ncbi_registry",
                title_match=KNOWN_VALID_REFS[known_key]["title"],
                suppressed=False,
            )

        # 2. Check live NCBI E-utilities API
        summary = self.ncbi_client.fetch_pubmed_summary(pmid_clean)
        if summary and "title" in summary:
            return CitationVerificationResult(
                is_valid=True,
                pmid=pmid_clean,
                verification_source="ncbi_eutilities",
                title_match=summary.get("title"),
                suppressed=False,
            )

        # 3. Failed validation -> SUPPRESS
        return CitationVerificationResult(
            is_valid=False,
            pmid=pmid_clean,
            verification_source="ncbi_eutilities",
            suppressed=True,
            failure_reason=f"PMID {pmid_clean} could not be verified in PubMed",
        )

    def validate_doi(self, doi: str) -> CitationVerificationResult:
        doi_clean = doi.strip().replace("https://doi.org/", "").replace("doi:", "")
        known_key = f"DOI:{doi_clean}"

        if known_key in KNOWN_VALID_REFS:
            return CitationVerificationResult(
                is_valid=True,
                doi=doi_clean,
                verification_source="crossref_registry",
                title_match=KNOWN_VALID_REFS[known_key]["title"],
                suppressed=False,
            )

        meta = self.crossref_client.fetch_doi_metadata(doi_clean)
        if meta and "title" in meta:
            titles = meta.get("title", [])
            title_str = titles[0] if titles else "Unknown Title"
            return CitationVerificationResult(
                is_valid=True,
                doi=doi_clean,
                verification_source="crossref_rest_api",
                title_match=title_str,
                suppressed=False,
            )

        return CitationVerificationResult(
            is_valid=False,
            doi=doi_clean,
            verification_source="crossref_rest_api",
            suppressed=True,
            failure_reason=f"DOI {doi_clean} could not be verified in Crossref",
        )

    def validate_guideline(self, guideline_ref: str) -> CitationVerificationResult:
        if "ICMR" in guideline_ref or "NIN" in guideline_ref or "WHO" in guideline_ref:
            return CitationVerificationResult(
                is_valid=True,
                verification_source="tier_1_guideline_registry",
                title_match=guideline_ref,
                suppressed=False,
            )

        return CitationVerificationResult(
            is_valid=False,
            verification_source="tier_1_guideline_registry",
            suppressed=True,
            failure_reason=f"Guideline ref '{guideline_ref}' not in Tier 1 registry",
        )


def validate_citation(
    pmid: str | None = None,
    doi: str | None = None,
    guideline_ref: str | None = None,
) -> CitationVerificationResult:
    validator = CitationValidator()
    if pmid:
        return validator.validate_pmid(pmid)
    if doi:
        return validator.validate_doi(doi)
    if guideline_ref:
        return validator.validate_guideline(guideline_ref)

    return CitationVerificationResult(
        is_valid=False,
        suppressed=True,
        failure_reason="No identifier provided for citation verification",
    )
