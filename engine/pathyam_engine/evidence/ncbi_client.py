"""NCBI E-utilities API client for PubMed and PMC retrieval & validation.

Provides programmatic interface to NCBI E-utilities REST API:
  esearch: Find PMIDs for query terms
  esummary: Fetch article metadata and verification details by PMID
  efetch: Retrieve full text XML/abstracts
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

__all__ = ["NCBIClient"]

_NCBI_ESUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
_NCBI_ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
_NCBI_EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

# E-utilities allows 3 requests/second unauthenticated, 10/second with an API key.
# Set NCBI_API_KEY to raise the ceiling; nothing here retries on a 429.
_API_KEY_ENV = "NCBI_API_KEY"


class NCBIClient:
    """Client for NCBI E-utilities API."""

    def __init__(
        self,
        email: str = "dev@pathyam.ai",
        tool: str = "pathyam_evidence_engine",
        api_key: str | None = None,
    ) -> None:
        self.email = email
        self.tool = tool
        self.api_key = api_key or os.environ.get(_API_KEY_ENV)
        self._cache: dict[str, dict[str, Any]] = {}

    def _params(self, **kw: str) -> dict[str, str]:
        params = {"tool": self.tool, "email": self.email, **kw}
        if self.api_key:
            params["api_key"] = self.api_key
        return params

    def _get(self, url: str, params: dict[str, str], timeout: int = 8) -> bytes | None:
        full = f"{url}?{urllib.parse.urlencode(params)}"
        try:
            req = urllib.request.Request(full, headers={"User-Agent": f"Pathyam/{self.tool}"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception:
            return None

    def fetch_pubmed_summaries(self, pmids: list[str]) -> dict[str, dict[str, Any]]:
        """Batch esummary. Returns {pmid: docsummary} for those that resolved."""
        clean = [p.strip() for p in pmids if p.strip().isdigit()]
        if not clean:
            return {}

        raw = self._get(
            _NCBI_ESUMMARY_URL,
            self._params(db="pubmed", id=",".join(clean), retmode="json"),
        )
        if raw is None:
            return {}

        try:
            result = json.loads(raw.decode("utf-8")).get("result", {})
        except (ValueError, UnicodeDecodeError):
            return {}

        out: dict[str, dict[str, Any]] = {}
        for pmid in clean:
            doc = result.get(pmid)
            # esummary reports unresolvable ids as a doc carrying an "error" key.
            if isinstance(doc, dict) and "error" not in doc and doc.get("title"):
                out[pmid] = doc
        return out

    def fetch_abstracts(self, pmids: list[str]) -> dict[str, str]:
        """Batch efetch of abstract text. Missing abstracts are simply absent."""
        clean = [p.strip() for p in pmids if p.strip().isdigit()]
        if not clean:
            return {}

        raw = self._get(
            _NCBI_EFETCH_URL,
            self._params(db="pubmed", id=",".join(clean), retmode="xml"),
        )
        if raw is None:
            return {}

        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            return {}

        out: dict[str, str] = {}
        for article in root.findall(".//PubmedArticle"):
            pmid_el = article.find(".//MedlineCitation/PMID")
            if pmid_el is None or not pmid_el.text:
                continue
            # An abstract may be split into labelled sections (BACKGROUND, METHODS...).
            parts = [
                "".join(el.itertext()).strip()
                for el in article.findall(".//Abstract/AbstractText")
            ]
            text = " ".join(p for p in parts if p)
            if text:
                out[pmid_el.text.strip()] = text
        return out

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

    def search_pubmed(
        self,
        query: str,
        max_results: int = 5,
        sort: str | None = None,
    ) -> list[str]:
        """Search PubMed for PMIDs matching query. ``sort='date'`` for most recent first."""
        kw = {"db": "pubmed", "term": query, "retmax": str(max_results), "retmode": "json"}
        if sort:
            kw["sort"] = sort

        raw = self._get(_NCBI_ESEARCH_URL, self._params(**kw))
        if raw is None:
            return []
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return []
        return list(data.get("esearchresult", {}).get("idlist", []))
