"""Build the clinical evidence corpus from PubMed.

Every document is assembled from what E-utilities returns for a PMID -- title,
abstract, journal, authors, year. None of it is typed by hand.

That is the structural fix for what shipped as EVIDENCE_004: a document whose
fields come from the registry under a given identifier cannot claim a title that
identifier does not resolve to. The previous corpus was written into a Python list
and the PMIDs attached afterwards, which is how one of them ended up pointing at a
paper on electrical impedance tomography.

Run after the schema is applied:

    python3 -m pathyam_engine.authoring evidence --dsn "$PATHYAM_DSN"

The queries below are deliberately narrow and are recorded on each row
(``retrieved_via``), so the corpus can be re-derived, audited, or challenged. They
are a starting point chosen for this product's domain, not a systematic review --
which is a real limitation, noted in the README rather than papered over.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..evidence.ncbi_client import NCBIClient

__all__ = ["CORPUS_QUERIES", "EvidenceIngestResult", "build_corpus_from_pubmed"]

# topic key -> PubMed query. `hasabstract` is required: a document with no abstract
# contributes a title to the index and nothing retrievable underneath it.
CORPUS_QUERIES: dict[str, str] = {
    "south_indian_glycemic_index": (
        '("glycemic index"[tiab] OR "glycaemic index"[tiab] OR "glycemic load"[tiab]) '
        'AND ("idli"[tiab] OR "dosa"[tiab] OR "South India"[tiab] OR "Indian"[tiab]) '
        'AND hasabstract'
    ),
    "batter_fermentation": (
        '(("idli"[tiab] OR "dosa"[tiab] OR "batter"[tiab]) AND "fermentation"[tiab]) '
        'AND hasabstract'
    ),
    "millet_glycemic_response": (
        '("finger millet"[tiab] OR "ragi"[tiab] OR "millet"[tiab]) '
        'AND ("glycemic"[tiab] OR "glycaemic"[tiab]) AND hasabstract'
    ),
    "pulses_postprandial_glucose": (
        '("pulses"[tiab] OR "legume"[tiab] OR "dal"[tiab] OR "black gram"[tiab]) '
        'AND "postprandial"[tiab] AND "glucose"[tiab] AND hasabstract'
    ),
    "dietary_fibre_glycemia": (
        '("dietary fiber"[tiab] OR "dietary fibre"[tiab] OR "viscous fiber"[tiab]) '
        'AND "postprandial"[tiab] AND ("glycemia"[tiab] OR "glucose"[tiab]) '
        'AND hasabstract'
    ),
    "coconut_oil_lipids": (
        '"coconut oil"[tiab] AND ("lipid"[tiab] OR "cholesterol"[tiab]) AND hasabstract'
    ),
    "indian_diet_type2_diabetes": (
        '("Indian"[tiab] AND "diet"[tiab]) AND "type 2 diabetes"[tiab] AND hasabstract'
    ),
}

_MIN_ABSTRACT_CHARS = 200


@dataclass
class EvidenceIngestResult:
    queries_run: int = 0
    documents_inserted: int = 0
    documents_updated: int = 0
    skipped_no_abstract: int = 0
    skipped: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "queries_run": self.queries_run,
            "documents_inserted": self.documents_inserted,
            "documents_updated": self.documents_updated,
            "skipped_no_abstract": self.skipped_no_abstract,
            "skipped": self.skipped,
        }


def _year_from(pubdate: str) -> int | None:
    match = re.search(r"(1[89]\d{2}|20\d{2})", pubdate or "")
    return int(match.group(1)) if match else None


def build_corpus_from_pubmed(
    conn,
    *,
    client: NCBIClient | None = None,
    queries: dict[str, str] | None = None,
    per_query: int = 6,
    dry_run: bool = False,
) -> EvidenceIngestResult:
    """Populate ``ref.evidence_document`` from PubMed.

    Idempotent on PMID: re-running refreshes a document in place rather than
    duplicating it, and records the query that most recently retrieved it.
    """
    client = client or NCBIClient()
    queries = queries or CORPUS_QUERIES
    result = EvidenceIngestResult()

    with conn.cursor() as cur:
        for topic, query in queries.items():
            result.queries_run += 1

            pmids = client.search_pubmed(query, max_results=per_query, sort="relevance")
            if not pmids:
                result.skipped.append(f"{topic}: no results (or PubMed unreachable)")
                continue

            summaries = client.fetch_pubmed_summaries(pmids)
            abstracts = client.fetch_abstracts(list(summaries))

            for pmid, doc in summaries.items():
                abstract = abstracts.get(pmid, "")
                if len(abstract) < _MIN_ABSTRACT_CHARS:
                    # A title alone is not evidence: there is nothing under it for
                    # retrieval to match against or for a reader to check.
                    result.skipped_no_abstract += 1
                    continue

                authors = [a.get("name", "") for a in doc.get("authors", []) if a.get("name")]
                doi = next(
                    (a.get("value") for a in doc.get("articleids", [])
                     if a.get("idtype") == "doi"),
                    None,
                )

                cur.execute(
                    """INSERT INTO ref.evidence_document
                           (tier, pmid, doi, title, abstract, journal, authors,
                            publication_year, retrieved_via, retrieved_at)
                       VALUES ('TIER_2_PUBMED', %s, %s, %s, %s, %s, %s, %s, %s, now())
                       ON CONFLICT (pmid) DO UPDATE
                           SET title = EXCLUDED.title,
                               abstract = EXCLUDED.abstract,
                               journal = EXCLUDED.journal,
                               authors = EXCLUDED.authors,
                               publication_year = EXCLUDED.publication_year,
                               doi = EXCLUDED.doi,
                               retrieved_via = EXCLUDED.retrieved_via,
                               retrieved_at = now()
                       RETURNING (xmax = 0) AS inserted""",
                    (
                        pmid, doi, doc.get("title", "").rstrip("."), abstract,
                        doc.get("fulljournalname") or doc.get("source"),
                        authors, _year_from(doc.get("pubdate", "")), topic,
                    ),
                )
                row = cur.fetchone()
                if row and row[0]:
                    result.documents_inserted += 1
                else:
                    result.documents_updated += 1

    if dry_run:
        conn.rollback()
    else:
        conn.commit()
    return result
