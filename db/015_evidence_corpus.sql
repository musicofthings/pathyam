-- ============================================================================
-- Pathyam · 015 · Clinical evidence corpus
-- ============================================================================
-- The evidence layer previously retrieved from three documents hardcoded in a
-- Python list, using term overlap, behind a docstring that advertised BM25 and
-- pgvector. This puts the corpus where it belongs and gives it one real retrieval
-- arm: PostgreSQL full-text search.
--
-- Every row is fetched from a registry rather than written by hand, and carries the
-- identifier it came from. That is the whole point: a document whose title and
-- abstract were retrieved from PubMed under a given PMID cannot disagree with what
-- that PMID resolves to, which is the failure mode that produced EVIDENCE_004.
--
-- NOT DONE HERE: semantic retrieval. That needs an embedding column and a provider
-- this repository has no credentials for. Adding a stub that returned lexical
-- results under a semantic name is exactly what was removed, so there is one arm and
-- it is named for what it is.
-- ============================================================================

CREATE TABLE IF NOT EXISTS ref.evidence_document (
    evidence_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    -- Provenance tier, mirroring the source hierarchy the product already uses.
    tier             text NOT NULL CHECK (tier IN
                         ('TIER_1_GUIDELINES','TIER_2_PUBMED','TIER_3_PMC',
                          'TIER_4_CROSSREF')),

    -- At least one identifier. A document with none cannot be verified, and an
    -- unverifiable document has no business in a clinical evidence corpus.
    pmid             text UNIQUE,
    doi              text,
    guideline_ref    text,

    title            text NOT NULL,
    abstract         text,
    journal          text,
    authors          text[] NOT NULL DEFAULT '{}',
    publication_year smallint,

    -- Which query pulled it in, so the corpus can be re-derived and audited.
    retrieved_via    text,
    retrieved_at     timestamptz NOT NULL DEFAULT now(),

    -- Lexical retrieval. Title weighted above abstract: a paper whose TITLE is about
    -- glycemic index is more on-point for that query than one that mentions it once
    -- in its methods.
    search_tsv       tsvector GENERATED ALWAYS AS (
                         setweight(to_tsvector('english', coalesce(title, '')), 'A')
                      || setweight(to_tsvector('english', coalesce(abstract, '')), 'B')
                     ) STORED,

    CONSTRAINT evidence_has_identifier_ck
        CHECK (num_nonnulls(pmid, doi, guideline_ref) >= 1)
);

CREATE INDEX IF NOT EXISTS evidence_search_idx
    ON ref.evidence_document USING GIN (search_tsv);
CREATE INDEX IF NOT EXISTS evidence_tier_idx
    ON ref.evidence_document (tier);

COMMENT ON TABLE ref.evidence_document IS
'Clinical evidence corpus. Rows are retrieved from a registry (PubMed via '
'E-utilities) rather than authored, so a document cannot claim a title its own '
'identifier does not resolve to.';

COMMENT ON COLUMN ref.evidence_document.search_tsv IS
'Lexical retrieval vector, title weighted A and abstract B. This is the only '
'retrieval arm — there is no embedding column and no semantic search.';
