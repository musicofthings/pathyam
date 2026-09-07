"""Adversarial tests for citation verification.

The case these exist for: EVIDENCE_004 shipped citing PMID 31234567 for "Dietary
Fiber and Postprandial Glycemic Response in Type 2 Diabetes". That PMID is real and
belongs to a paper on electrical impedance tomography of granular material. Existence
checking passed it, because the identifier does resolve.

These use stub clients throughout -- no network -- so the suite is deterministic and
does not depend on NCBI being reachable.
"""

from __future__ import annotations

import pytest

from pathyam_engine.evidence.citation_validator import (
    CONTRADICTED,
    UNVERIFIED,
    VERIFIED,
    CitationValidator,
    title_agreement,
)


class _StubNCBI:
    """Returns a fixed esummary record, or None to simulate an outage."""

    def __init__(self, record=None):
        self._record = record

    def fetch_pubmed_summary(self, pmid):
        return self._record


class _StubCrossref:
    def __init__(self, record=None):
        self._record = record

    def fetch_doi_metadata(self, doi):
        return self._record


# The genuine record behind PMID 31234567.
_TOMOGRAPHY_PAPER = {
    "title": ("Assessment of the Spatial Distribution of Moisture Content in Granular "
              "Material Using Electrical Impedance Tomography."),
    "authors": [{"name": "Rymarczyk T"}, {"name": "Klosowski G"}],
    "pubdate": "2019 Jun",
}

_REAL_GLYCEMIC_PAPER = {
    "title": ("Glycemic carbohydrates, glycemic index, and glycemic load of commonly "
              "consumed South Indian breakfast foods."),
    "authors": [{"name": "Shakappa D"}, {"name": "Naik R"}, {"name": "Sobhana PP"}],
    "pubdate": "2022 Sep",
}


def _validator(ncbi=None, crossref=None):
    return CitationValidator(ncbi_client=_StubNCBI(ncbi),
                             crossref_client=_StubCrossref(crossref))


# ------------------------------------------------------- the shipped defect ----

def test_the_fabricated_citation_that_shipped_is_now_caught():
    """PMID 31234567 resolves, but not to the paper EVIDENCE_004 claimed."""
    result = _validator(ncbi=_TOMOGRAPHY_PAPER).validate_pmid(
        "31234567",
        claimed_title="Dietary Fiber and Postprandial Glycemic Response in Type 2 Diabetes",
        claimed_authors=["Vijayakumar A", "Dubasi HB"],
        claimed_year=2021,
    )

    assert result.status == CONTRADICTED, (
        "an identifier resolving to a different work must be CONTRADICTED, not merely "
        "unverified — this is the case that shipped"
    )
    assert not result.is_valid
    assert result.suppressed
    assert "Electrical Impedance Tomography" in result.title_match
    assert "different work" in result.failure_reason


def test_a_correct_citation_verifies():
    result = _validator(ncbi=_REAL_GLYCEMIC_PAPER).validate_pmid(
        "35875218",
        claimed_title=("Glycemic carbohydrates, glycemic index and glycemic load of "
                       "commonly consumed South Indian breakfast foods"),
        claimed_authors=["Shakappa D", "Naik R", "Sobhana PP"],
        claimed_year=2022,
    )
    assert result.status == VERIFIED
    assert result.is_valid and not result.suppressed


# --------------------------------------------------- outage is not a verdict ----

def test_an_unreachable_registry_is_unverified_never_contradicted():
    """An outage must not be able to accuse a good citation of being wrong."""
    result = _validator(ncbi=None).validate_pmid(
        "35875218", claimed_title="Anything at all",
    )
    assert result.status == UNVERIFIED
    assert result.status != CONTRADICTED
    assert not result.is_valid, "unverified must not render as a citation"


def test_a_resolving_identifier_with_nothing_to_check_is_not_verified():
    """Existence alone is the old, insufficient test — it must not read as VERIFIED."""
    result = _validator(ncbi=_REAL_GLYCEMIC_PAPER).validate_pmid("35875218")
    assert result.status == UNVERIFIED
    assert "no title to check" in result.failure_reason


# ------------------------------------------------------- secondary evidence ----

def test_a_matching_title_with_entirely_different_authors_is_not_verified():
    result = _validator(ncbi=_REAL_GLYCEMIC_PAPER).validate_pmid(
        "35875218",
        claimed_title=("Glycemic carbohydrates, glycemic index, and glycemic load of "
                       "commonly consumed South Indian breakfast foods."),
        claimed_authors=["Nobody X", "Nobody Y"],
    )
    assert result.status == UNVERIFIED
    assert "author surname" in result.failure_reason


def test_online_first_year_drift_of_one_still_verifies():
    result = _validator(ncbi=_REAL_GLYCEMIC_PAPER).validate_pmid(
        "35875218",
        claimed_title=("Glycemic carbohydrates, glycemic index, and glycemic load of "
                       "commonly consumed South Indian breakfast foods."),
        claimed_authors=["Shakappa D"],
        claimed_year=2021,
    )
    assert result.status == VERIFIED


# ------------------------------------------------------------- identifiers ----

@pytest.mark.parametrize("bad", ["not-a-pmid", "PMID:abc", "", "12.34"])
def test_a_malformed_pmid_is_contradicted_without_a_network_call(bad):
    assert _validator().validate_pmid(bad).status == CONTRADICTED


def test_a_doi_resolving_to_a_different_work_is_contradicted():
    crossref = {
        "title": ["Something else entirely about polymer rheology"],
        "author": [{"family": "Nobody"}],
        "issued": {"date-parts": [[2015]]},
    }
    result = _validator(crossref=crossref).validate_doi(
        "10.1007/s13197-022-05368-6",
        claimed_title="Glycemic index of South Indian foods",
    )
    assert result.status == CONTRADICTED


# --------------------------------------------------------- guideline registry ----

def test_a_registered_guideline_verifies():
    assert _validator().validate_guideline("ICMR-NIN-DGI-2024 Clause 3.2").status == VERIFIED


def test_a_string_merely_containing_who_is_no_longer_accepted():
    """The old check was a substring test, so this passed as a Tier 1 guideline."""
    result = _validator().validate_guideline("WHOLE GRAIN COUNCIL")
    assert result.status == CONTRADICTED
    assert "not in the Tier 1 guideline registry" in result.failure_reason


# ------------------------------------------------------------------ scoring ----

def test_title_agreement_separates_formatting_from_a_different_paper():
    same = title_agreement(
        "Glycemic carbohydrates, glycemic index and glycemic load of South Indian foods",
        "Glycemic carbohydrates, glycemic index, and glycemic load of South Indian foods.",
    )
    different = title_agreement(
        "Dietary Fiber and Postprandial Glycemic Response in Type 2 Diabetes",
        _TOMOGRAPHY_PAPER["title"],
    )
    assert same > 0.9
    assert different < 0.45
    assert same - different > 0.4, "the two cases must be comfortably separable"
