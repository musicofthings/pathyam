-- ============================================================================
-- Pathyam · 001 · Extensions, schemas, enums, helper functions
-- PostgreSQL 16+
-- ============================================================================
-- Schema separation is deliberate and load-bearing:
--   ref  — reference data. Publishable, citable, no personal data. Ever.
--   app  — user data. Personal data under DPDP Act 2023. Never published.
--   ml   — inference traces and training corpus. Derived from app, gated by consent.
-- If a table is in the wrong schema, the compliance story breaks silently.
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS vector;        -- pgvector: dish/ingredient embeddings

CREATE SCHEMA IF NOT EXISTS ref;
CREATE SCHEMA IF NOT EXISTS app;
CREATE SCHEMA IF NOT EXISTS ml;

COMMENT ON SCHEMA ref IS 'Reference data: the publishable Pathyam-DB. No personal data.';
COMMENT ON SCHEMA app IS 'User data. Personal data under DPDP Act 2023.';
COMMENT ON SCHEMA ml  IS 'Inference traces and training corpus. Consent-gated at query time.';

-- ---------------------------------------------------------------- enums ----

-- Confidence tier is the differentiator. Every nutrient value carries one.
--   A = directly analysed for this exact food (IFCT 2017, or our own lab panel)
--   B = calculated from tier-A ingredients via documented recipe + yield + retention
--   C = borrowed from a foreign food composition table
--   D = imputed from a similar food, or back-calculated
-- Clinical outputs default to A and B only.
CREATE TYPE ref.confidence_tier AS ENUM ('A', 'B', 'C', 'D');

CREATE TYPE ref.value_basis AS ENUM ('per_100g', 'per_100ml', 'per_serving', 'per_piece');

CREATE TYPE ref.param_dtype AS ENUM ('continuous', 'integer', 'categorical', 'boolean');

-- Distribution families for parameter priors. Monte Carlo samples from these.
CREATE TYPE ref.prior_dist AS ENUM (
    'point',        -- {"value": 8.0}
    'normal',       -- {"mu": 12, "sigma": 3}
    'lognormal',    -- {"mu": 2.08, "sigma": 0.55}   (mu, sigma of ln X)
    'uniform',      -- {"lo": 4, "hi": 12}
    'categorical',  -- {"categories": ["gingelly","coconut"], "weights": [0.6,0.4]}
    'beta'          -- {"alpha": 2, "beta": 5}
);

-- Where a parameter value came from. Precedence order at inference time is:
--   user_stated > user_prior > geometry > vlm > regional_prior > population_prior > template_default
CREATE TYPE ref.param_source AS ENUM (
    'user_stated',
    'user_prior',
    'geometry',
    'vlm',
    'regional_prior',
    'population_prior',
    'template_default'
);

CREATE TYPE app.log_method AS ENUM ('photo', 'text', 'voice', 'barcode', 'manual', 'copy');

-- ------------------------------------------------------ helper functions ----

-- Name normalisation for fuzzy multilingual matching.
-- Deliberately does NOT use the unaccent extension: [:alnum:] is UTF-8 aware and
-- matches Indic scripts, so this works for native script and romanised forms alike.
-- Declared IMMUTABLE so it can back a generated column and a functional index.
CREATE OR REPLACE FUNCTION ref.normalize_name(p_text text)
RETURNS text
LANGUAGE sql
IMMUTABLE STRICT PARALLEL SAFE
AS $$
    SELECT btrim(
             regexp_replace(
               regexp_replace(lower(p_text), '[^[:alnum:] ]+', '', 'g'),
               '\s+', ' ', 'g'));
$$;

COMMENT ON FUNCTION ref.normalize_name(text) IS
'Lowercase, strip punctuation, collapse whitespace. UTF-8 aware, so Tamil/Telugu/'
'Malayalam/Kannada native script survives intact. Used for generated match columns.';

-- Validates that a prior_params JSONB payload has the keys its distribution needs.
-- Enforced by CHECK constraints wherever a distribution is stored.
CREATE OR REPLACE FUNCTION ref.valid_prior_params(p_dist ref.prior_dist, p_params jsonb)
RETURNS boolean
LANGUAGE sql
IMMUTABLE PARALLEL SAFE
AS $$
    SELECT CASE p_dist
        WHEN 'point'       THEN p_params ? 'value'
        WHEN 'normal'      THEN p_params ?& ARRAY['mu','sigma']
        WHEN 'lognormal'   THEN p_params ?& ARRAY['mu','sigma']
        WHEN 'uniform'     THEN p_params ?& ARRAY['lo','hi']
        WHEN 'categorical' THEN p_params ?& ARRAY['categories','weights']
                                AND jsonb_array_length(p_params->'categories')
                                  = jsonb_array_length(p_params->'weights')
        WHEN 'beta'        THEN p_params ?& ARRAY['alpha','beta']
        ELSE false
    END;
$$;

COMMENT ON FUNCTION ref.valid_prior_params(ref.prior_dist, jsonb) IS
'Structural validation of a prior distribution payload. Catches malformed priors at '
'write time rather than at Monte Carlo time, where the failure would be silent.';
