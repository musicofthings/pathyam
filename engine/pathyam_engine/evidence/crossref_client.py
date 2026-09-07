"""Crossref REST API client for DOI verification & metadata lookup."""

from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request
from typing import Any

__all__ = ["CrossrefClient"]

_CROSSREF_API_URL = "https://api.crossref.org/works/"

# Crossref publishes no hard public limit but asks for considerate use and returns
# 429 under load. One request per 100 ms is well inside anything they signal.
_MIN_INTERVAL = 0.1


class CrossrefClient:
    """Client for official Crossref REST API."""

    def __init__(self, mailto: str = "dev@pathyam.ai") -> None:
        self.mailto = mailto
        self._cache: dict[str, dict[str, Any]] = {}
        self._last_request = 0.0
        self._lock = threading.Lock()

    def _throttle(self) -> None:
        with self._lock:
            wait = _MIN_INTERVAL - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()

    def fetch_doi_metadata(self, doi: str) -> dict[str, Any] | None:
        """Verify DOI and retrieve work metadata."""
        clean_doi = doi.strip().replace("https://doi.org/", "").replace("doi:", "")
        if not clean_doi:
            return None

        if clean_doi in self._cache:
            return self._cache[clean_doi]

        encoded_doi = urllib.parse.quote(clean_doi, safe="")
        url = f"{_CROSSREF_API_URL}{encoded_doi}?mailto={urllib.parse.quote(self.mailto)}"

        self._throttle()
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": f"Pathyam/1.0 (mailto:{self.mailto})"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                message = data.get("message", {})
                if message:
                    self._cache[clean_doi] = message
                    return message
        except Exception:
            pass

        return None
