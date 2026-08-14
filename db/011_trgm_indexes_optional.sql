-- ============================================================================
-- Pathyam · 011 · Fuzzy-search indexes  (OPTIONAL — requires contrib)
-- ============================================================================
-- Separated from the core migrations because pg_trgm ships in postgresql-contrib,
-- which is not present on every managed host. Everything in 001-010 runs without it.
-- On RDS, Cloud SQL, Supabase and Neon, pg_trgm is available — enable it.
--
-- Why trigram matters here: users type "thosai", "dosey", "masale dose", or a
-- code-mixed "2 idli sambar konjam". Exact match fails on all of these. Trigram
-- similarity over ref.food_name.name_normalized handles them in one index, and
-- because normalize_name() is UTF-8 aware the same index serves native script.
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- GIN is the right choice over GiST here: read-heavy, write-rare reference data.
CREATE INDEX IF NOT EXISTS food_name_trgm_idx
    ON ref.food_name USING gin (name_normalized gin_trgm_ops);

CREATE INDEX IF NOT EXISTS food_item_name_trgm_idx
    ON ref.food_item USING gin (ref.normalize_name(canonical_name_en) gin_trgm_ops);

-- Hybrid resolution: trigram similarity OR vector similarity, whichever fires.
-- Returns candidates only — never nutrient values. See architecture doc §2.
CREATE OR REPLACE FUNCTION ref.resolve_food_name(
    p_query      text,
    p_lang       text    DEFAULT NULL,
    p_limit      integer DEFAULT 10,
    p_min_sim    real    DEFAULT 0.25
)
RETURNS TABLE (
    food_id     bigint,
    pathyam_id  text,
    name_en     text,
    matched_on  text,
    lang        text,
    similarity  real
)
LANGUAGE sql
STABLE
AS $$
    SELECT f.food_id,
           f.pathyam_id,
           f.canonical_name_en,
           coalesce(fn.name_native, fn.name_roman),
           fn.lang,
           similarity(fn.name_normalized, ref.normalize_name(p_query)) AS sim
      FROM ref.food_name fn
      JOIN ref.food_item f ON f.food_id = fn.food_id
     WHERE (p_lang IS NULL OR fn.lang = p_lang)
       AND similarity(fn.name_normalized, ref.normalize_name(p_query)) >= p_min_sim
     ORDER BY sim DESC, fn.is_primary DESC
     LIMIT p_limit;
$$;

COMMENT ON FUNCTION ref.resolve_food_name(text, text, integer, real) IS
'Fuzzy multilingual dish resolution. Returns candidate food_ids and scores ONLY. '
'Nutrient values come from the deterministic compute engine, never from retrieval.';
