"""Citation verification: does the identifier resolve to the work being cited?

WHAT CHANGED AND WHY
--------------------
The previous version asked only whether an identifier *existed*. That is not the
question. A citation is wrong in the way that matters when the PMID is real but
belongs to a different paper -- which is exactly what shipped in this corpus:

    EVIDENCE_004 cited PMID 31234567 for
        "Dietary Fiber and Postprandial Glycemic Response in Type 2 Diabetes"
    PMID 31234567 is
        "Assessment of the Spatial Distribution of Moisture Content in Granular
         Material Using Electrical Impedance Tomography"

The identifier resolved, so existence checking passed it. The claim had been
written first and a plausible-looking identifier attached afterwards, and nothing
in the pipeline could tell.

So verification now compares the *record* against the *claim*: title, authors and
year. And the outcome is three-valued, because "we could not check" and "we checked
and it is wrong" are different facts that were previously collapsed into one:

    VERIFIED      the record matches the claim
    UNVERIFIED    we could not reach the registry -- says nothing about the claim
    CONTRADICTED  the identifier resolves to a DIFFERENT work

CONTRADICTED is the serious one. It means someone attached an identifier to a claim
it does not support, and it should fail a build rather than quietly drop a citation.
An outage must never be able to produce it.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Any

from .crossref_client import CrossrefClient
from .ncbi_client import NCBIClient
from .protocol import CitationVerificationResult

__all__ = [
    "VERIFIED",
    "UNVERIFIED",
    "CONTRADICTED",
    "CitationValidator",
    "GUIDELINE_REGISTRY",
    "title_agreement",
    "validate_citation",
]

VERIFIED = "VERIFIED"
UNVERIFIED = "UNVERIFIED"
CONTRADICTED = "CONTRADICTED"

# Titles agreeing at or above this are the same work. Chosen with headroom on both
# sides: registries vary in subtitle, punctuation and trailing period, so an exact
# match is too strict; the EVIDENCE_004 case scores 0.19, so there is a wide gap
# between "same paper, formatted differently" and "different paper entirely".
_TITLE_MATCH = 0.75
# Below this, the titles are not the same work and the citation is contradicted.
# Between the two, we decline to judge rather than guess.
_TITLE_MISMATCH = 0.45
# Online-first publication routinely shifts a year, so allow one either way.
_YEAR_SLACK = 1

_STOPWORDS = {"a", "an", "the", "of", "and", "in", "on", "for", "with", "to"}


def _normalise_title(title: str) -> str:
    text = re.sub(r"[^a-z0-9 ]+", " ", (title or "").lower())
    return " ".join(w for w in text.split() if w not in _STOPWORDS)


def title_agreement(claimed: str, actual: str) -> float:
    """Similarity between a claimed title and the registry's, 0.0-1.0."""
    a, b = _normalise_title(claimed), _normalise_title(actual)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _surnames(authors: list[str]) -> set[str]:
    """First token of each author string, lowercased. Handles 'Shakappa D' and
    Crossref's {'family': 'Shakappa'} once flattened by the caller."""
    out = set()
    for author in authors or ():
        token = re.split(r"[,\s]+", (author or "").strip())
        if token and token[0]:
            out.add(token[0].lower())
    return out


def _year_from(text: str) -> int | None:
    match = re.search(r"(1[89]\d{2}|20\d{2})", text or "")
    return int(match.group(1)) if match else None


# Tier 1 guidelines, by explicit identifier. The previous implementation accepted any
# string containing "ICMR", "NIN" or "WHO", so "WHOLE GRAIN COUNCIL" passed as a Tier
# 1 clinical guideline. Membership here is a deliberate act.
GUIDELINE_REGISTRY: dict[str, str] = {
    "ICMR-NIN-DGI-2024": "Dietary Guidelines for Indians 2024. ICMR-NIN, Hyderabad.",
    "ICMR-NIN-RDA-2020": (
        "Nutrient Requirements and Recommended Dietary Allowances for Indians 2020. "
        "ICMR-NIN, Hyderabad."
    ),
    "IFCT2017": (
        "Longvah T, Ananthan R, Bhaskarachary K, Venkaiah K. Indian Food Composition "
        "Tables 2017. ICMR-NIN, Hyderabad."
    ),
}


@dataclass(frozen=True)
class _Record:
    """What a registry says a given identifier actually is."""

    title: str
    authors: list[str]
    year: int | None


class CitationValidator:
    """Checks that an identifier resolves to the work a claim says it does."""

    def __init__(
        self,
        ncbi_client: NCBIClient | None = None,
        crossref_client: CrossrefClient | None = None,
    ) -> None:
        self.ncbi_client = ncbi_client or NCBIClient()
        self.crossref_client = crossref_client or CrossrefClient()

    # ------------------------------------------------------------- PubMed --

    def validate_pmid(
        self,
        pmid: str,
        *,
        claimed_title: str | None = None,
        claimed_authors: list[str] | None = None,
        claimed_year: int | None = None,
    ) -> CitationVerificationResult:
        clean = (pmid or "").strip().replace("PMID:", "").replace("pmid:", "").strip()
        if not clean.isdigit():
            return self._result(CONTRADICTED, pmid=clean, source="ncbi",
                                reason=f"{pmid!r} is not a PubMed identifier")

        summary = self.ncbi_client.fetch_pubmed_summary(clean)
        if summary is None:
            # Could be an outage or a genuinely absent record; esummary does not let
            # us tell, and guessing in either direction is worse than saying so.
            return self._result(UNVERIFIED, pmid=clean, source="ncbi",
                                reason="PubMed did not respond, or has no such record")

        record = _Record(
            title=summary.get("title", ""),
            authors=[a.get("name", "") for a in summary.get("authors", [])],
            year=_year_from(summary.get("pubdate", "")),
        )
        return self._compare(record, claimed_title, claimed_authors, claimed_year,
                             pmid=clean, source="ncbi")

    # ------------------------------------------------------------ Crossref --

    def validate_doi(
        self,
        doi: str,
        *,
        claimed_title: str | None = None,
        claimed_authors: list[str] | None = None,
        claimed_year: int | None = None,
    ) -> CitationVerificationResult:
        clean = (doi or "").strip().replace("https://doi.org/", "").replace("doi:", "")
        if not clean:
            return self._result(CONTRADICTED, doi=clean, source="crossref",
                                reason="empty DOI")

        meta = self.crossref_client.fetch_doi_metadata(clean)
        if meta is None:
            return self._result(UNVERIFIED, doi=clean, source="crossref",
                                reason="Crossref did not respond, or has no such record")

        titles = meta.get("title") or []
        parts = (meta.get("issued") or {}).get("date-parts") or [[]]
        record = _Record(
            title=titles[0] if titles else "",
            authors=[a.get("family", "") for a in meta.get("author", []) if a.get("family")],
            year=parts[0][0] if parts and parts[0] else None,
        )
        return self._compare(record, claimed_title, claimed_authors, claimed_year,
                             doi=clean, source="crossref")

    # ----------------------------------------------------------- guideline --

    def validate_guideline(self, guideline_ref: str) -> CitationVerificationResult:
        """Match against an explicit registry, not a substring test."""
        ref = (guideline_ref or "").strip()
        # A reference may carry a clause: "ICMR-NIN-DGI-2024 Clause 3.2".
        identifier = ref.split()[0] if ref else ""
        citation = GUIDELINE_REGISTRY.get(identifier)
        if citation is None:
            return self._result(
                CONTRADICTED, source="guideline_registry",
                reason=(f"{identifier or ref!r} is not in the Tier 1 guideline "
                        f"registry; known: {sorted(GUIDELINE_REGISTRY)}"),
            )
        return self._result(VERIFIED, source="guideline_registry", title=citation)

    # ------------------------------------------------------------ internals --

    def _compare(
        self,
        record: _Record,
        claimed_title: str | None,
        claimed_authors: list[str] | None,
        claimed_year: int | None,
        *,
        pmid: str | None = None,
        doi: str | None = None,
        source: str = "",
    ) -> CitationVerificationResult:
        if not record.title:
            return self._result(UNVERIFIED, pmid=pmid, doi=doi, source=source,
                                reason="registry returned a record with no title")

        # Nothing to compare against. The identifier resolves, which is all we know,
        # and calling that VERIFIED would restore the old false confidence.
        if not claimed_title:
            return self._result(
                UNVERIFIED, pmid=pmid, doi=doi, source=source, title=record.title,
                reason="identifier resolves, but the claim carried no title to check "
                       "it against",
            )

        agreement = title_agreement(claimed_title, record.title)

        if agreement >= _TITLE_MATCH:
            mismatches = self._secondary_mismatches(record, claimed_authors, claimed_year)
            if mismatches:
                return self._result(
                    UNVERIFIED, pmid=pmid, doi=doi, source=source, title=record.title,
                    reason=f"title matches ({agreement:.2f}) but {', '.join(mismatches)}",
                )
            return self._result(VERIFIED, pmid=pmid, doi=doi, source=source,
                                title=record.title, score=agreement)

        if agreement < _TITLE_MISMATCH:
            return self._result(
                CONTRADICTED, pmid=pmid, doi=doi, source=source, title=record.title,
                reason=(f"resolves to a different work (title agreement "
                        f"{agreement:.2f}): claimed {claimed_title!r}, "
                        f"registry says {record.title!r}"),
                score=agreement,
            )

        return self._result(
            UNVERIFIED, pmid=pmid, doi=doi, source=source, title=record.title,
            reason=(f"title agreement {agreement:.2f} is inconclusive: claimed "
                    f"{claimed_title!r}, registry says {record.title!r}"),
            score=agreement,
        )

    @staticmethod
    def _secondary_mismatches(
        record: _Record, claimed_authors: list[str] | None, claimed_year: int | None
    ) -> list[str]:
        problems: list[str] = []
        if claimed_authors:
            claimed = _surnames(claimed_authors)
            actual = _surnames(record.authors)
            if claimed and actual and not (claimed & actual):
                problems.append(
                    f"no author surname in common (claimed {sorted(claimed)}, "
                    f"registry {sorted(actual)})"
                )
        if claimed_year and record.year and abs(claimed_year - record.year) > _YEAR_SLACK:
            problems.append(f"year {claimed_year} vs registry {record.year}")
        return problems

    @staticmethod
    def _result(
        status: str,
        *,
        pmid: str | None = None,
        doi: str | None = None,
        source: str = "",
        title: str | None = None,
        reason: str | None = None,
        score: float | None = None,
    ) -> CitationVerificationResult:
        return CitationVerificationResult(
            is_valid=(status == VERIFIED),
            status=status,
            pmid=pmid,
            doi=doi,
            verification_source=source,
            title_match=title,
            # Only a verified citation is rendered. Both other states withhold it,
            # but for different reasons, and the reason is recorded.
            suppressed=(status != VERIFIED),
            failure_reason=reason,
            agreement=score,
        )


def validate_citation(
    pmid: str | None = None,
    doi: str | None = None,
    guideline_ref: str | None = None,
    *,
    claimed_title: str | None = None,
    claimed_authors: list[str] | None = None,
    claimed_year: int | None = None,
) -> CitationVerificationResult:
    validator = CitationValidator()
    if pmid:
        return validator.validate_pmid(pmid, claimed_title=claimed_title,
                                       claimed_authors=claimed_authors,
                                       claimed_year=claimed_year)
    if doi:
        return validator.validate_doi(doi, claimed_title=claimed_title,
                                      claimed_authors=claimed_authors,
                                      claimed_year=claimed_year)
    if guideline_ref:
        return validator.validate_guideline(guideline_ref)

    return CitationValidator._result(
        CONTRADICTED, reason="no identifier provided for citation verification"
    )
