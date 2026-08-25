"""NCBI E-utilities API client for PubMed and PMC retrieval & validation.

Provides programmatic interface to NCBI E-utilities REST API:
  esearch: Find PMIDs for query terms
  esummary: Fetch article metadata and verification details by PMID
  efetch: Retrieve full text XML/abstracts
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

__all__ = ["NCBIClient"]

_NCBI_ESUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
_NCBI_ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"


class NCBIClient:
    """Client for NCBI E-utilities API."""

    def __init__(self, email: str = "dev@pathyam.ai", tool: str = "pathyam_evidence_engine") -> None:
        self.email = email
        self.tool = tool
        self._cache: dict[str, dict[str, Any]] = {}

    def fetch_pubmed_summary(self, pmid: str) -> dict[str, Any] | None:
        """Fetch article metadata by PMID via esummary."""
        pmid_clean = pmid.strip().replace("PMID:", "").replace("pmid:", "")
        if not pmid_clean.isdigit():
            return None

        if pmid_clean in self._cache:
            return self._cache[pmid_clean]

        params = {
            "db": "pubmed",
            "id": pmid_clean,
            "retmode": "json",
            "tool": self.tool,
            "email": self.email,
        }
        url = f"{_NCBI_ESUMMARY_URL}?{urllib.parse.urlencode(params)}"

        try:
            req = urllib.request.Request(url, headers={"User-Agent": f"Pathyam/{self.tool}"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                raw_bytes = resp.read()
                data = json.loads(raw_bytes.decode("utf-8"))
                result_map = data.get("result", {})
                doc_summary = result_map.get(pmid_clean)
                if doc_summary:
                    self._cache[pmid_clean] = doc_summary
                    return doc_summary
        except Exception:
            pass

        return None

    def search_pubmed(self, query: str, max_results: int = 5) -> list[str]:
        """Search PubMed for PMIDs matching query."""
        params = {
            "db": "pubmed",
            "term": query,
            "retmax": str(max_results),
            "retmode": "json",
            "tool": self.tool,
            "email": self.email,
        }
        url = f"{_NCBI_ESEARCH_URL}?{urllib.parse.urlencode(params)}"

        try:
            req = urllib.request.Request(url, headers={"User-Agent": f"Pathyam/{self.tool}"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                id_list = data.get("esearchresult", {}).get("idlist", [])
                return list(id_list)
        except Exception:
            return []
