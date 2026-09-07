-- Pathyam Full Database Migration & Seed Master Script
-- Auto-generated for Supabase / Neon / PostgreSQL 16+
-------------------------------------------------------

-- === Begin 001_extensions_schemas_enums.sql ===
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

-- === End 001_extensions_schemas_enums.sql ===


-- === Begin 002_sources_nutrients_regions.sql ===
-- ============================================================================
-- Pathyam · 002 · Sources, nutrients, regions
-- ============================================================================

-- ---------------------------------------------------------------- source ----
-- Every number in ref.* traces back to exactly one row here. No exceptions.
CREATE TABLE ref.source (
    source_id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_key             text        NOT NULL UNIQUE,   -- 'IFCT2017', 'PMID:35875218'
    citation               text        NOT NULL,
    doi_or_url             text,
    licence                text        NOT NULL,
    licence_verified_on    date,
    -- The compliance canary. Defaults to FALSE so that anything not explicitly
    -- cleared shows up in ref.v_uncleared_values until someone does the legal work.
    is_commercial_cleared  boolean     NOT NULL DEFAULT false,
    permission_ref         text,                          -- pointer to the signed permission document
    retrieved_on           date,
    notes                  text,
    created_at             timestamptz NOT NULL DEFAULT now()
);

COMMENT ON COLUMN ref.source.is_commercial_cleared IS
'FALSE until written permission for commercial use exists. ref.v_uncleared_values '
'lists every nutrient value currently resting on an uncleared source — run it before any release.';

-- -------------------------------------------------------------- nutrient ----
CREATE TABLE ref.nutrient (
    nutrient_id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    infoods_tagname    text     NOT NULL UNIQUE,   -- 'ENERC_KCAL','PROCNT','K','CHOAVLDF'
    name               text     NOT NULL,
    unit               text     NOT NULL,          -- 'kcal','g','mg','ug'
    decimals           smallint NOT NULL DEFAULT 2 CHECK (decimals BETWEEN 0 AND 6),
    nutrient_group     text     NOT NULL CHECK (nutrient_group IN
                           ('proximate','carbohydrate','lipid','mineral','vitamin',
                            'amino_acid','fatty_acid','bioactive','other')),
    parent_nutrient_id bigint   REFERENCES ref.nutrient(nutrient_id),
    is_core            boolean  NOT NULL DEFAULT false,  -- shown on the basic panel
    display_order      smallint
);

COMMENT ON COLUMN ref.nutrient.infoods_tagname IS
'FAO/INFOODS tagname. Do NOT invent local nutrient codes — retrofitting identifiers '
'across a populated composition table is a rewrite, not a migration.';

CREATE INDEX nutrient_group_idx ON ref.nutrient (nutrient_group, display_order);

-- ---------------------------------------------------------------- region ----
-- Regional resolution is the entire product thesis: IFCT 2017 composites across six
-- national regions, which erases exactly the Kerala-coconut vs Tamil-gingelly signal.
CREATE TABLE ref.region (
    region_id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    region_key       text NOT NULL UNIQUE,        -- 'TN-CHETTINAD', 'KL-MALABAR'
    state            text NOT NULL,
    sub_region       text,
    parent_region_id bigint REFERENCES ref.region(region_id),
    iso_3166_2       text,                        -- 'IN-TN'
    notes            text
);

CREATE INDEX region_parent_idx ON ref.region (parent_region_id);
CREATE INDEX region_state_idx  ON ref.region (state);

-- === End 002_sources_nutrients_regions.sql ===


-- === Begin 003_foods_composition_lexicon.sql ===
-- ============================================================================
-- Pathyam · 003 · Food items, composition values, multilingual lexicon, embeddings
-- ============================================================================

-- ------------------------------------------------------------- food_item ----
CREATE TABLE ref.food_item (
    food_id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- Stable public identifier. Survives internal renumbering; this is what a
    -- published dataset, a citation, or a B2B API contract refers to.
    pathyam_id         text        NOT NULL UNIQUE
                       CHECK (pathyam_id ~ '^PY-F-[0-9]{6}$'),
    canonical_name_en  text        NOT NULL,
    scientific_name    text,
    food_group         text        NOT NULL,
    is_recipe          boolean     NOT NULL DEFAULT false,

    -- Ontology alignment. Populate from day one (see architecture doc §4).
    foodon_iri         text,                    -- 'http://purl.obolibrary.org/obo/FOODON_03301123'
    langual_codes      text[],                  -- {'A0785','B1329','H0200'}
    foodex2_code       text,

    -- Source-system coding, for traceability back to the original tables
    ifct_code          text,
    indb_code          text,
    usda_fdc_id        integer,
    cofid_code         text,

    -- Physical properties needed by the volume → mass path
    edible_portion_pct numeric(5,2) CHECK (edible_portion_pct > 0 AND edible_portion_pct <= 100),
    density_g_per_ml   numeric(6,3) CHECK (density_g_per_ml > 0),
    density_source_id  bigint REFERENCES ref.source(source_id),

    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now()
);

COMMENT ON COLUMN ref.food_item.density_g_per_ml IS
'Required to convert an estimated volume (katori, or a geometry module output) into '
'mass. Missing density is the commonest silent cause of portion error.';

CREATE INDEX food_item_group_idx  ON ref.food_item (food_group);
CREATE INDEX food_item_foodon_idx ON ref.food_item (foodon_iri) WHERE foodon_iri IS NOT NULL;
CREATE INDEX food_item_ifct_idx   ON ref.food_item (ifct_code)  WHERE ifct_code  IS NOT NULL;
CREATE INDEX food_item_langual_idx ON ref.food_item USING gin (langual_codes);

-- ----------------------------------------------------- composition_value ----
CREATE TABLE ref.composition_value (
    value_id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    food_id               bigint NOT NULL REFERENCES ref.food_item(food_id) ON DELETE CASCADE,
    nutrient_id           bigint NOT NULL REFERENCES ref.nutrient(nutrient_id),

    -- NUMERIC, not double precision. Reproducibility is the point: a clinical value
    -- that changes in the 15th decimal between runs is a value you cannot defend.
    value                 numeric(14,5) NOT NULL,
    basis                 ref.value_basis NOT NULL DEFAULT 'per_100g',

    -- Analytical dispersion, where the source reports it. Feeds Monte Carlo so that
    -- composition uncertainty propagates alongside parameter uncertainty.
    sd                    numeric(14,5) CHECK (sd >= 0),
    n_samples             integer CHECK (n_samples > 0),

    confidence            ref.confidence_tier NOT NULL,
    source_id             bigint NOT NULL REFERENCES ref.source(source_id),
    analytical_method     text,

    is_borrowed           boolean NOT NULL DEFAULT false,
    borrowed_from_food_id bigint REFERENCES ref.food_item(food_id),

    valid_from            date NOT NULL DEFAULT current_date,
    valid_to              date,
    notes                 text,

    CONSTRAINT composition_borrowed_ck CHECK (
        (is_borrowed = false AND borrowed_from_food_id IS NULL)
     OR (is_borrowed = true  AND borrowed_from_food_id IS NOT NULL
                            AND confidence IN ('C','D'))
    ),
    CONSTRAINT composition_validity_ck CHECK (valid_to IS NULL OR valid_to > valid_from),
    CONSTRAINT composition_unique_ck UNIQUE (food_id, nutrient_id, basis, valid_from)
);

COMMENT ON CONSTRAINT composition_borrowed_ck ON ref.composition_value IS
'A borrowed value must name what it was borrowed from and cannot claim tier A or B. '
'This is the constraint that keeps the confidence tiering honest.';

CREATE INDEX composition_food_idx     ON ref.composition_value (food_id, nutrient_id)
                                      WHERE valid_to IS NULL;
CREATE INDEX composition_nutrient_idx ON ref.composition_value (nutrient_id, value)
                                      WHERE valid_to IS NULL;
CREATE INDEX composition_source_idx   ON ref.composition_value (source_id);
CREATE INDEX composition_conf_idx     ON ref.composition_value (confidence);

-- ------------------------------------------------------------- food_name ----
-- The four-language lexicon. No public dataset contains this; it is proprietary IP.
CREATE TABLE ref.food_name (
    name_id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    food_id         bigint NOT NULL REFERENCES ref.food_item(food_id) ON DELETE CASCADE,
    lang            text   NOT NULL CHECK (lang IN ('en','ta','te','ml','kn')),
    name_native     text,          -- தோசை / దోస / ദോശ / ದೋಸೆ
    name_roman      text,          -- dosai / dosa / thosai / dose
    -- Generated match key. Both native and romanised forms normalise through the
    -- same function, so a single trigram index serves all five languages.
    name_normalized text GENERATED ALWAYS AS
                        (ref.normalize_name(coalesce(name_roman, name_native, ''))) STORED,
    region_id       bigint REFERENCES ref.region(region_id),
    is_primary      boolean NOT NULL DEFAULT false,
    is_colloquial   boolean NOT NULL DEFAULT false,
    source_id       bigint REFERENCES ref.source(source_id),

    CONSTRAINT food_name_has_form_ck CHECK (num_nonnulls(name_native, name_roman) >= 1),
    CONSTRAINT food_name_unique_ck   UNIQUE (food_id, lang, name_normalized)
);

CREATE INDEX food_name_lookup_idx  ON ref.food_name (name_normalized);
CREATE INDEX food_name_food_idx    ON ref.food_name (food_id, lang);
CREATE UNIQUE INDEX food_name_primary_idx ON ref.food_name (food_id, lang)
                                          WHERE is_primary;

-- ------------------------------------------------------- food_embedding ----
-- Dimension is pinned to the model. Changing embedding model = new column/table,
-- not an in-place update: mixing dimensions silently corrupts ANN results.
CREATE TABLE ref.food_embedding (
    food_id    bigint PRIMARY KEY REFERENCES ref.food_item(food_id) ON DELETE CASCADE,
    model      text        NOT NULL,
    dim        integer     NOT NULL CHECK (dim = 1024),
    embedding  vector(1024) NOT NULL,
    source_text text        NOT NULL,   -- exactly what was embedded, for reproducibility
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX food_embedding_hnsw_idx ON ref.food_embedding
    USING hnsw (embedding vector_cosine_ops);

-- === End 003_foods_composition_lexicon.sql ===


-- === Begin 004_recipe_templates.sql ===
-- ============================================================================
-- Pathyam · 004 · Parametric recipe templates
-- ============================================================================
-- The core design decision. Dosa is not a recipe, it is a family:
--   4 states × 6 styles × 3 cooking fats × 2 rice types × {plain, filled} = 288 rows
-- Enumerating that across ~400 dish families gives ~115,000 hand-curated rows.
--
-- Instead: ~400 TEMPLATES with typed, distributed PARAMETERS. A specific dosa is a
-- parameter binding, not a row. Curation collapses ~300×, the VLM becomes a parameter
-- estimator rather than a 5,000-class classifier, uncertainty propagation is native,
-- and per-user personalisation is a prior update rather than a schema change.
-- ============================================================================

CREATE TABLE ref.recipe_template (
    template_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pathyam_id       text NOT NULL UNIQUE CHECK (pathyam_id ~ '^PY-T-[0-9]{6}$'),
    food_id          bigint NOT NULL REFERENCES ref.food_item(food_id),
    base_method      text NOT NULL CHECK (base_method IN
                        ('raw','steamed','boiled','simmered','shallow_fried','deep_fried',
                         'roasted','griddled','fermented','tempered','ground','assembled')),
    langual_facets   text[],
    default_servings numeric(6,2) NOT NULL DEFAULT 1 CHECK (default_servings > 0),
    -- Cooked weight ÷ raw weight. NULL means "derive from the ingredient-level
    -- yield_factor rows" rather than "assume 1.0" — the distinction matters.
    yield_factor     numeric(6,4) CHECK (yield_factor > 0 AND yield_factor <= 5),
    source_id        bigint REFERENCES ref.source(source_id),
    version          integer NOT NULL DEFAULT 1,
    is_active        boolean NOT NULL DEFAULT true,
    notes            text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX recipe_template_food_idx ON ref.recipe_template (food_id) WHERE is_active;

-- ----------------------------------------------------- template_parameter ----
CREATE TABLE ref.template_parameter (
    parameter_id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    template_id           bigint NOT NULL REFERENCES ref.recipe_template(template_id) ON DELETE CASCADE,
    -- Constrained to a safe identifier because it is substituted into qty_expr,
    -- which is evaluated by the compute engine. Never relax this.
    param_name            text NOT NULL CHECK (param_name ~ '^[a-z][a-z0-9_]{0,39}$'),
    dtype                 ref.param_dtype NOT NULL,
    unit                  text,
    prior_dist            ref.prior_dist NOT NULL,
    prior_params          jsonb NOT NULL,

    -- Can a vision model plausibly observe this? Drives which parameters the
    -- perception layer is asked to estimate and which fall back to priors.
    observable_from_image boolean NOT NULL DEFAULT false,

    -- The single question to ask when this parameter dominates output variance.
    -- Asking one targeted question collapses more uncertainty than asking three.
    elicitation_question  text,
    elicitation_options   jsonb,   -- [{"label":"little","value":4},{"label":"generous","value":14}]

    display_order         smallint,
    notes                 text,

    CONSTRAINT template_parameter_unique_ck UNIQUE (template_id, param_name),
    CONSTRAINT template_parameter_prior_ck
        CHECK (ref.valid_prior_params(prior_dist, prior_params)),
    CONSTRAINT template_parameter_categorical_ck
        CHECK (dtype <> 'categorical' OR prior_dist = 'categorical')
);

COMMENT ON COLUMN ref.template_parameter.observable_from_image IS
'FALSE for cooking oil, fermentation time and rice type — the parameters that dominate '
'energy variance and that no vision model can see. This column is why the app asks '
'a question instead of silently guessing.';

-- ---------------------------------------------------- template_ingredient ----
CREATE TABLE ref.template_ingredient (
    template_ingredient_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    template_id            bigint NOT NULL REFERENCES ref.recipe_template(template_id) ON DELETE CASCADE,

    -- Exactly one of: a leaf ingredient, or a nested sub-recipe.
    -- Sub-recipes are how "idli sambar" contains "sambar" contains "sambar podi".
    food_id                bigint REFERENCES ref.food_item(food_id),
    sub_template_id        bigint REFERENCES ref.recipe_template(template_id),

    -- Arithmetic expression over this template's declared parameters.
    -- Evaluated by the compute engine with simpleeval/asteval — NEVER Python eval().
    qty_expr               text NOT NULL,
    unit                   text NOT NULL DEFAULT 'g',
    preparation_state      text,   -- 'raw','soaked','fermented','roasted','ground'
    cooking_method         text,   -- drives retention-factor lookup
    is_optional            boolean NOT NULL DEFAULT false,
    display_order          smallint,

    CONSTRAINT template_ingredient_target_ck
        CHECK (num_nonnulls(food_id, sub_template_id) = 1),
    CONSTRAINT template_ingredient_no_self_ck
        CHECK (sub_template_id IS NULL OR sub_template_id <> template_id)
);

CREATE INDEX template_ingredient_tpl_idx ON ref.template_ingredient (template_id);
CREATE INDEX template_ingredient_sub_idx ON ref.template_ingredient (sub_template_id)
                                          WHERE sub_template_id IS NOT NULL;

-- --------------------------------------------------------- regional_prior ----
-- "Kerala" is not 288 extra rows. It is a shifted prior on fat_type and rice_type.
CREATE TABLE ref.regional_prior (
    regional_prior_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    template_id       bigint NOT NULL REFERENCES ref.recipe_template(template_id) ON DELETE CASCADE,
    region_id         bigint NOT NULL REFERENCES ref.region(region_id),
    param_name        text   NOT NULL,
    prior_dist        ref.prior_dist NOT NULL,
    prior_params      jsonb  NOT NULL,
    source_id         bigint REFERENCES ref.source(source_id),
    notes             text,

    CONSTRAINT regional_prior_unique_ck UNIQUE (template_id, region_id, param_name),
    CONSTRAINT regional_prior_valid_ck  CHECK (ref.valid_prior_params(prior_dist, prior_params))
);

-- ------------------------------------------- retention and yield factors ----
CREATE TABLE ref.retention_factor (
    retention_factor_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    food_group          text   NOT NULL,
    cooking_method      text   NOT NULL,
    nutrient_id         bigint NOT NULL REFERENCES ref.nutrient(nutrient_id),
    -- Can exceed 100: water loss concentrates nutrients per unit mass.
    pct_retained        numeric(6,2) NOT NULL CHECK (pct_retained >= 0 AND pct_retained <= 300),
    source_id           bigint NOT NULL REFERENCES ref.source(source_id),
    CONSTRAINT retention_unique_ck UNIQUE (food_group, cooking_method, nutrient_id)
);

COMMENT ON TABLE ref.retention_factor IS
'USDA Table of Nutrient Retention Factors R6 (public domain), cross-checked against '
'Bognar 2002. Mandatory — you cannot compute a cooked dish without it.';

CREATE TABLE ref.yield_factor (
    yield_factor_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    food_group      text NOT NULL,
    cooking_method  text NOT NULL,
    factor          numeric(6,4) NOT NULL CHECK (factor > 0 AND factor <= 5),
    source_id       bigint NOT NULL REFERENCES ref.source(source_id),
    CONSTRAINT yield_unique_ck UNIQUE (food_group, cooking_method)
);

-- === End 004_recipe_templates.sql ===


-- === Begin 005_servings_glycemic.sql ===
-- ============================================================================
-- Pathyam · 005 · Serving units and glycemic values
-- ============================================================================

-- ----------------------------------------------------------- serving_unit ----
-- There is NO national standard katori size. Dietary questionnaires commonly use
-- katori = 150 ml, glass = 250 ml, cup = 200 ml — but a North Indian katori is
-- larger than a South Indian one, and Gujarati and Bengali katoris differ again.
-- Every competitor treats "1 katori" as a constant. Making it region-scoped and
-- user-calibratable is a correctness fix, not a nicety.
CREATE TABLE ref.serving_unit (
    unit_id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    unit_key            text NOT NULL UNIQUE,        -- 'katori_south','idli_piece'
    name_en             text NOT NULL,
    name_i18n           jsonb NOT NULL DEFAULT '{}', -- {"ta":"கிண்ணம்","ml":"കിണ്ണം"}
    volume_ml           numeric(8,2) CHECK (volume_ml > 0),
    typical_g           numeric(8,2) CHECK (typical_g > 0),
    -- Uncertainty on the unit itself, which propagates into every portion using it.
    typical_g_sd        numeric(8,2) CHECK (typical_g_sd >= 0),
    applies_to_group    text,
    region_id           bigint REFERENCES ref.region(region_id),
    -- Count-based units are how South Indians actually describe intake:
    -- "3 idli", "1 dosa", "2 vada". Gram-first logging is why existing apps feel foreign.
    is_count_based      boolean NOT NULL DEFAULT false,
    source_id           bigint REFERENCES ref.source(source_id),

    CONSTRAINT serving_unit_measure_ck CHECK (num_nonnulls(volume_ml, typical_g) >= 1)
);

CREATE INDEX serving_unit_region_idx ON ref.serving_unit (region_id, applies_to_group);

-- Links a specific food or dish to its natural serving units, with real gram weights.
CREATE TABLE ref.food_serving (
    food_serving_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    food_id         bigint NOT NULL REFERENCES ref.food_item(food_id) ON DELETE CASCADE,
    unit_id         bigint NOT NULL REFERENCES ref.serving_unit(unit_id),
    grams           numeric(8,2) NOT NULL CHECK (grams > 0),
    grams_sd        numeric(8,2) CHECK (grams_sd >= 0),
    region_id       bigint REFERENCES ref.region(region_id),
    is_default      boolean NOT NULL DEFAULT false,
    source_id       bigint REFERENCES ref.source(source_id),

    CONSTRAINT food_serving_unique_ck UNIQUE (food_id, unit_id, region_id)
);

CREATE INDEX food_serving_food_idx ON ref.food_serving (food_id);

-- --------------------------------------------------------- glycemic_value ----
-- The clinical differentiator. No existing database keys South Indian GI/GL to a
-- composition table. Seeded from Shakappa et al. 2022 (PMID 35875218, ICMR-NIN
-- Dept of Dietetics) and the CFTRI starch-digestibility literature.
CREATE TABLE ref.glycemic_value (
    gi_id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    food_id         bigint NOT NULL REFERENCES ref.food_item(food_id) ON DELETE CASCADE,
    gi              numeric(5,2) CHECK (gi >= 0 AND gi <= 200),
    gi_sem          numeric(5,2) CHECK (gi_sem >= 0),
    gi_reference    text NOT NULL DEFAULT 'glucose'
                    CHECK (gi_reference IN ('glucose','white_bread')),
    gl_per_serving  numeric(6,2) CHECK (gl_per_serving >= 0),
    serving_g       numeric(8,2),
    glycemic_cho_pct numeric(5,2),
    n_subjects      integer CHECK (n_subjects > 0),
    population      text,                  -- 'healthy adults, South India'
    method          text,                  -- 'FAO/WHO 1998'
    pmid            text,
    doi             text,
    source_id       bigint NOT NULL REFERENCES ref.source(source_id),
    confidence      ref.confidence_tier NOT NULL,

    CONSTRAINT glycemic_has_value_ck CHECK (num_nonnulls(gi, gl_per_serving) >= 1)
);

CREATE INDEX glycemic_food_idx ON ref.glycemic_value (food_id);

-- === End 005_servings_glycemic.sql ===


-- === Begin 006_users_consent.sql ===
-- ============================================================================
-- Pathyam · 006 · Users and consent  (DPDP Act 2023 + DPDP Rules 2025)
-- ============================================================================
-- The launch plan is: ship on public data, then fine-tune on user-contributed
-- images and corrections. That plan has a legal precondition.
--
-- DPDP requires consent that is "free, specific, informed, unconditional and
-- unambiguous". A bundled "we may use your data to improve our services" clause in
-- the ToS is almost certainly NOT specific enough to cover model training. The
-- failure mode is accumulating 100,000 labelled images that cannot lawfully be used.
--
-- The design response, implemented below:
--   1. model_training is a SEPARATE consent purpose with its own record.
--   2. Training eligibility is DERIVED AT QUERY TIME (ml.v_training_eligible_user),
--      never materialised. Withdrawal takes effect on the next query, not on the
--      next batch job.
--   3. Consent records are retained 7 years per the Rules, so a user row is never
--      hard-deleted — it is anonymised (app.anonymise_user).
-- ============================================================================

CREATE TABLE app.app_user (
    user_id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at      timestamptz NOT NULL DEFAULT now(),
    region_id       bigint REFERENCES ref.region(region_id),
    preferred_lang  text NOT NULL DEFAULT 'en' CHECK (preferred_lang IN ('en','ta','te','ml','kn')),
    year_of_birth   smallint CHECK (year_of_birth BETWEEN 1900 AND 2100),
    sex_at_birth    text CHECK (sex_at_birth IN ('female','male','intersex','undisclosed')),
    -- Deliberately NO email / phone / name here. Direct identifiers live in a
    -- separate, access-restricted, encrypted table so that analytics and ML
    -- workloads can be granted app.app_user without ever touching contact data.
    is_anonymised   boolean NOT NULL DEFAULT false,
    deleted_at      timestamptz
);

COMMENT ON TABLE app.app_user IS
'Pseudonymous user record. Direct identifiers (email, phone, name) belong in a '
'separate restricted table, not here — so ML and analytics roles can read this safely.';

-- ------------------------------------------------------- consent purposes ----
CREATE TABLE app.consent_purpose (
    purpose_id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    purpose_key             text NOT NULL UNIQUE,
    description_en          text NOT NULL,
    description_i18n        jsonb NOT NULL DEFAULT '{}',
    is_required_for_service boolean NOT NULL DEFAULT false,
    created_at              timestamptz NOT NULL DEFAULT now()
);

INSERT INTO app.consent_purpose (purpose_key, description_en, is_required_for_service) VALUES
  ('core_service',
   'Store your meal logs so the app can show your intake and history.', true),
  ('model_training',
   'Use your food photos and corrections to improve Pathyam''s recognition models.', false),
  ('research_publication',
   'Include your de-identified data in aggregate research outputs and publications.', false),
  ('clinician_sharing',
   'Share your logs with a clinician or dietitian you nominate.', false),
  ('marketing',
   'Send you product updates and offers.', false);

-- ---------------------------------------------------------- user consent ----
CREATE TABLE app.user_consent (
    consent_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id         uuid   NOT NULL REFERENCES app.app_user(user_id) ON DELETE RESTRICT,
    purpose_id      bigint NOT NULL REFERENCES app.consent_purpose(purpose_id),
    granted_at      timestamptz NOT NULL DEFAULT now(),
    withdrawn_at    timestamptz,
    -- Which notice text, in which language, the user actually saw. Without this you
    -- cannot demonstrate that consent was "informed".
    notice_version  text NOT NULL,
    notice_lang     text NOT NULL CHECK (notice_lang IN ('en','ta','te','ml','kn')),
    evidence        jsonb NOT NULL,   -- UI snapshot, timestamp, request metadata
    -- DPDP Rules 2025: consent, notice and sharing records retained >= 7 years.
    -- NOT a generated column: timestamptz + interval is STABLE, not IMMUTABLE
    -- (the result depends on the session TimeZone), and PostgreSQL rejects
    -- non-immutable generation expressions. A BEFORE trigger is the correct tool.
    retain_until    date NOT NULL,

    CONSTRAINT user_consent_window_ck CHECK (withdrawn_at IS NULL OR withdrawn_at >= granted_at),
    CONSTRAINT user_consent_retention_ck CHECK (retain_until >= (granted_at AT TIME ZONE 'UTC')::date)
);

CREATE OR REPLACE FUNCTION app.set_consent_retention()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    -- Pinned to UTC so the retention date does not drift with session timezone.
    NEW.retain_until := ((NEW.granted_at AT TIME ZONE 'UTC') + interval '7 years')::date;
    RETURN NEW;
END;
$$;

CREATE TRIGGER user_consent_retention_trg
    BEFORE INSERT OR UPDATE OF granted_at ON app.user_consent
    FOR EACH ROW EXECUTE FUNCTION app.set_consent_retention();

COMMENT ON COLUMN app.user_consent.user_id IS
'ON DELETE RESTRICT is intentional. Consent records must outlive the account (7 years). '
'Erasure is handled by app.anonymise_user(), not by DELETE.';

CREATE INDEX user_consent_lookup_idx ON app.user_consent (user_id, purpose_id)
                                     WHERE withdrawn_at IS NULL;
-- One live grant per user per purpose. Re-granting after withdrawal creates a new row.
CREATE UNIQUE INDEX user_consent_active_idx ON app.user_consent (user_id, purpose_id)
                                            WHERE withdrawn_at IS NULL;

-- ------------------------------------------------ erasure by anonymisation ----
CREATE OR REPLACE FUNCTION app.anonymise_user(p_user_id uuid)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    -- Withdraw every live consent first, so the training corpus view drops this
    -- user's rows immediately rather than at the next pipeline run.
    UPDATE app.user_consent
       SET withdrawn_at = now()
     WHERE user_id = p_user_id AND withdrawn_at IS NULL;

    UPDATE app.app_user
       SET year_of_birth = NULL,
           sex_at_birth  = NULL,
           region_id     = NULL,
           is_anonymised = true,
           deleted_at    = now()
     WHERE user_id = p_user_id;

    -- Detach stored media. The object-store deletion is the caller's responsibility
    -- and must be recorded separately; this only removes the pointer.
    UPDATE app.meal_log
       SET image_ref = NULL, notes = NULL, deleted_at = coalesce(deleted_at, now())
     WHERE user_id = p_user_id;
END;
$$;

COMMENT ON FUNCTION app.anonymise_user(uuid) IS
'DPDP erasure path. Withdraws consents (which instantly removes the user from the '
'training corpus view), strips attributes, and detaches media pointers — while '
'preserving the consent audit trail the Rules require for 7 years.';

-- === End 006_users_consent.sql ===


-- === Begin 007_meal_logs_inference.sql ===
-- ============================================================================
-- Pathyam · 007 · Meal logs, inference traces, user priors
-- ============================================================================
-- This file is the data flywheel. The critical insight: a user correction is only
-- usable for fine-tuning if you also stored WHAT THE APP PROPOSED and WHICH PRIOR
-- WAS ACTIVE at the time. Logging only the final value produces a corpus that
-- teaches a model nothing and cannot be used to check calibration.
-- ============================================================================

CREATE TABLE app.meal_log (
    meal_log_id  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      uuid NOT NULL REFERENCES app.app_user(user_id) ON DELETE RESTRICT,
    logged_at    timestamptz NOT NULL DEFAULT now(),
    consumed_at  timestamptz,
    meal_slot    text CHECK (meal_slot IN ('breakfast','tiffin','lunch','snack','dinner','other')),
    method       app.log_method NOT NULL,
    region_id    bigint REFERENCES ref.region(region_id),
    -- Object-store key only. Never store image bytes in Postgres.
    image_ref    text,
    image_sha256 bytea,
    notes        text,
    deleted_at   timestamptz
);

CREATE INDEX meal_log_user_time_idx ON app.meal_log (user_id, logged_at DESC)
                                    WHERE deleted_at IS NULL;

-- ----------------------------------------------------------- log line item ----
CREATE TABLE app.meal_log_item (
    item_id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    meal_log_id        uuid NOT NULL REFERENCES app.meal_log(meal_log_id) ON DELETE CASCADE,
    template_id        bigint REFERENCES ref.recipe_template(template_id),
    food_id            bigint REFERENCES ref.food_item(food_id),
    param_bindings     jsonb NOT NULL DEFAULT '{}',   -- {"fat_g": 8.2, "fat_type": "gingelly"}
    servings           numeric(8,3) NOT NULL DEFAULT 1 CHECK (servings > 0),
    unit_id            bigint REFERENCES ref.serving_unit(unit_id),

    -- Cached Monte Carlo output. Intervals, not point estimates — see architecture §7.
    energy_kcal_p50    numeric(10,2),
    energy_kcal_p10    numeric(10,2),
    energy_kcal_p90    numeric(10,2),
    nutrients          jsonb,   -- {"CHOAVLDF": {"p10":44,"p50":52,"p90":61}, ...}
    -- The parameter contributing most output variance. Drives the one question
    -- the app asks to collapse uncertainty.
    dominant_uncertainty_param text,
    engine_version     text,
    computed_at        timestamptz,

    is_user_confirmed  boolean NOT NULL DEFAULT false,
    confirmed_at       timestamptz,

    CONSTRAINT meal_log_item_target_ck CHECK (num_nonnulls(template_id, food_id) >= 1),
    CONSTRAINT meal_log_item_interval_ck CHECK (
        energy_kcal_p50 IS NULL
        OR (energy_kcal_p10 <= energy_kcal_p50 AND energy_kcal_p50 <= energy_kcal_p90))
);

COMMENT ON CONSTRAINT meal_log_item_interval_ck ON app.meal_log_item IS
'Guards the ordering of the credible interval. A p10 above p50 means the Monte Carlo '
'or the percentile extraction is broken — fail loudly at write time.';

CREATE INDEX meal_log_item_log_idx      ON app.meal_log_item (meal_log_id);
CREATE INDEX meal_log_item_template_idx ON app.meal_log_item (template_id);

-- ------------------------------------------------------- inference traces ----
CREATE TABLE ml.inference_run (
    run_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    item_id       uuid REFERENCES app.meal_log_item(item_id) ON DELETE CASCADE,
    stage         text NOT NULL CHECK (stage IN
                    ('vlm','retrieval','rerank','geometry','param_estimate','compute')),
    model_name    text NOT NULL,
    model_version text NOT NULL,
    prompt_version text,
    input_digest  bytea,      -- hash of the exact input, for reproducibility
    raw_output    jsonb,      -- structured VLM output, verbatim
    latency_ms    integer CHECK (latency_ms >= 0),
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX inference_run_item_idx  ON ml.inference_run (item_id, stage);
CREATE INDEX inference_run_model_idx ON ml.inference_run (model_name, model_version, created_at DESC);

-- Ranked candidates from retrieval/rerank. This is the training signal for the
-- reranker: when the user picks rank 3, that is a labelled hard negative for ranks 1-2.
CREATE TABLE ml.inference_candidate (
    run_id      uuid   NOT NULL REFERENCES ml.inference_run(run_id) ON DELETE CASCADE,
    rank        smallint NOT NULL CHECK (rank >= 1),
    template_id bigint REFERENCES ref.recipe_template(template_id),
    food_id     bigint REFERENCES ref.food_item(food_id),
    score       numeric(9,6) NOT NULL,
    PRIMARY KEY (run_id, rank),
    CONSTRAINT inference_candidate_target_ck CHECK (num_nonnulls(template_id, food_id) >= 1)
);

-- Per-parameter estimate WITH its provenance. Storing the source is what lets you
-- later ask "how good is the VLM at oil?" separately from "how good is the prior?".
CREATE TABLE ml.parameter_estimate (
    estimate_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    item_id     uuid NOT NULL REFERENCES app.meal_log_item(item_id) ON DELETE CASCADE,
    param_name  text NOT NULL,
    source      ref.param_source NOT NULL,
    dist        ref.prior_dist NOT NULL,
    dist_params jsonb NOT NULL,
    was_used    boolean NOT NULL DEFAULT false,   -- did this win the precedence contest?
    created_at  timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT parameter_estimate_unique_ck UNIQUE (item_id, param_name, source),
    CONSTRAINT parameter_estimate_valid_ck  CHECK (ref.valid_prior_params(dist, dist_params))
);

CREATE INDEX parameter_estimate_item_idx ON ml.parameter_estimate (item_id);

-- ---------------------------------------------------------- corrections ----
CREATE TABLE ml.user_correction (
    correction_id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    item_id         uuid NOT NULL REFERENCES app.meal_log_item(item_id) ON DELETE CASCADE,
    field           text NOT NULL,      -- 'template' | 'param:fat_g' | 'servings' | 'unit'
    proposed_value  jsonb,              -- what the app said  — REQUIRED for training value
    corrected_value jsonb NOT NULL,     -- what the user said
    -- Which run produced the proposal. Ties the correction back to a model version.
    run_id          uuid REFERENCES ml.inference_run(run_id) ON DELETE SET NULL,
    corrected_at    timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE ml.user_correction IS
'proposed_value + corrected_value + run_id is the triple that makes a correction '
'trainable. Storing only the final value yields a corpus with no learning signal.';

CREATE INDEX user_correction_item_idx  ON ml.user_correction (item_id);
CREATE INDEX user_correction_field_idx ON ml.user_correction (field, corrected_at DESC);

-- -------------------------------------------------- personalisation priors ----
-- The moat. A competitor can copy the dish list; they cannot copy six months of a
-- household's learned cooking-oil habit. Updated by conjugate Bayesian update on
-- every correction.
CREATE TABLE app.user_parameter_prior (
    user_id        uuid   NOT NULL REFERENCES app.app_user(user_id) ON DELETE RESTRICT,
    template_id    bigint NOT NULL REFERENCES ref.recipe_template(template_id) ON DELETE CASCADE,
    param_name     text   NOT NULL,
    dist           ref.prior_dist NOT NULL,
    dist_params    jsonb  NOT NULL,
    n_observations integer NOT NULL DEFAULT 0 CHECK (n_observations >= 0),
    updated_at     timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (user_id, template_id, param_name),
    CONSTRAINT user_parameter_prior_valid_ck CHECK (ref.valid_prior_params(dist, dist_params))
);

-- A global (non-user-specific) prior learned across the whole population.
-- Cold-start fallback before a user has enough observations of their own.
CREATE TABLE ref.population_prior (
    population_prior_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    template_id    bigint NOT NULL REFERENCES ref.recipe_template(template_id) ON DELETE CASCADE,
    param_name     text   NOT NULL,
    dist           ref.prior_dist NOT NULL,
    dist_params    jsonb  NOT NULL,
    n_observations integer NOT NULL DEFAULT 0,
    computed_at    timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT population_prior_unique_ck UNIQUE (template_id, param_name),
    CONSTRAINT population_prior_valid_ck  CHECK (ref.valid_prior_params(dist, dist_params))
);

-- === End 007_meal_logs_inference.sql ===


-- === Begin 008_functions.sql ===
-- ============================================================================
-- Pathyam · 008 · Functions: recipe expansion, provenance, cycle guard
-- ============================================================================
-- These are the queries the architecture document argued do not need a graph
-- database. At ~120,000 edges a recursive CTE returns in single-digit milliseconds.
-- Revisit only when the conditions in architecture §12 are met.
-- ============================================================================

-- ------------------------------------------------- recursive cycle guard ----
-- Sub-recipes nest (idli sambar → sambar → sambar podi). Nesting must not cycle,
-- and a CHECK constraint cannot express reachability, so this is a trigger.
CREATE OR REPLACE FUNCTION ref.assert_no_template_cycle()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_cycle boolean;
BEGIN
    IF NEW.sub_template_id IS NULL THEN
        RETURN NEW;
    END IF;

    WITH RECURSIVE reachable(template_id) AS (
        SELECT NEW.sub_template_id
        UNION
        SELECT ti.sub_template_id
          FROM ref.template_ingredient ti
          JOIN reachable r ON ti.template_id = r.template_id
         WHERE ti.sub_template_id IS NOT NULL
    )
    SELECT EXISTS (SELECT 1 FROM reachable WHERE template_id = NEW.template_id)
      INTO v_cycle;

    IF v_cycle THEN
        RAISE EXCEPTION
            'template cycle: template % cannot contain sub-template %',
            NEW.template_id, NEW.sub_template_id
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER template_ingredient_cycle_guard
    BEFORE INSERT OR UPDATE ON ref.template_ingredient
    FOR EACH ROW EXECUTE FUNCTION ref.assert_no_template_cycle();

-- ------------------------------------------------------- expand_template ----
-- Flattens a template into its leaf ingredients, carrying the chain of quantity
-- expressions along each path.
--
-- Deliberate design choice: this returns the EXPRESSION PATH as text[], not an
-- evaluated number. Symbolic arithmetic belongs in the compute engine (Python +
-- simpleeval over the sampled parameter vector), not in SQL. Trying to evaluate
-- qty_expr in SQL would mean either dynamic SQL — an injection surface — or a
-- bespoke expression parser in plpgsql. Neither is worth it.
CREATE OR REPLACE FUNCTION ref.expand_template(
    p_template_id bigint,
    p_max_depth   integer DEFAULT 6,
    p_include_optional boolean DEFAULT true
)
RETURNS TABLE (
    depth             integer,
    template_path     bigint[],
    leaf_food_id      bigint,
    leaf_pathyam_id   text,
    leaf_name_en      text,
    expr_path         text[],
    unit              text,
    preparation_state text,
    cooking_method    text,
    is_optional       boolean
)
LANGUAGE sql
STABLE
AS $$
    WITH RECURSIVE walk AS (
        -- seed: direct children of the requested template
        SELECT 1                                   AS depth,
               ARRAY[ti.template_id]               AS template_path,
               ti.food_id                          AS leaf_food_id,
               ti.sub_template_id,
               ARRAY[ti.qty_expr]                  AS expr_path,
               ti.unit,
               ti.preparation_state,
               ti.cooking_method,
               ti.is_optional
          FROM ref.template_ingredient ti
         WHERE ti.template_id = p_template_id
           AND (p_include_optional OR NOT ti.is_optional)

        UNION ALL

        -- recurse into sub-recipes, appending each level's expression
        SELECT w.depth + 1,
               w.template_path || ti.template_id,
               ti.food_id,
               ti.sub_template_id,
               w.expr_path || ti.qty_expr,
               ti.unit,
               ti.preparation_state,
               coalesce(ti.cooking_method, w.cooking_method),
               w.is_optional OR ti.is_optional
          FROM walk w
          JOIN ref.template_ingredient ti ON ti.template_id = w.sub_template_id
         WHERE w.sub_template_id IS NOT NULL
           AND w.depth < p_max_depth
           AND NOT (ti.template_id = ANY (w.template_path))   -- belt and braces
           AND (p_include_optional OR NOT ti.is_optional)
    )
    SELECT w.depth,
           w.template_path,
           w.leaf_food_id,
           f.pathyam_id,
           f.canonical_name_en,
           w.expr_path,
           w.unit,
           w.preparation_state,
           w.cooking_method,
           w.is_optional
      FROM walk w
      JOIN ref.food_item f ON f.food_id = w.leaf_food_id
     WHERE w.leaf_food_id IS NOT NULL          -- leaves only; sub-recipe nodes are traversal
     ORDER BY w.depth, f.canonical_name_en;
$$;

COMMENT ON FUNCTION ref.expand_template(bigint, integer, boolean) IS
'Flattens a parametric template to leaf ingredients with the chain of quantity '
'expressions. The compute engine evaluates expr_path against a sampled parameter '
'vector — SQL never evaluates user-authored expressions.';

-- ----------------------------------------------------- provenance_chain ----
-- "Where did this number come from?" — answerable for every value in the database.
-- This is the query that makes the confidence tiering meaningful to a clinician.
CREATE OR REPLACE FUNCTION ref.provenance_chain(p_food_id bigint)
RETURNS TABLE (
    hop            integer,
    food_id        bigint,
    pathyam_id     text,
    food_name      text,
    nutrient_tag   text,
    value          numeric,
    unit           text,
    confidence     ref.confidence_tier,
    source_key     text,
    citation       text,
    licence        text,
    commercial_ok  boolean
)
LANGUAGE sql
STABLE
AS $$
    WITH RECURSIVE chain AS (
        SELECT 0 AS hop, cv.food_id, cv.value_id, cv.borrowed_from_food_id
          FROM ref.composition_value cv
         WHERE cv.food_id = p_food_id AND cv.valid_to IS NULL

        UNION ALL

        SELECT c.hop + 1, cv.food_id, cv.value_id, cv.borrowed_from_food_id
          FROM chain c
          JOIN ref.composition_value cv ON cv.food_id = c.borrowed_from_food_id
         WHERE c.borrowed_from_food_id IS NOT NULL
           AND cv.valid_to IS NULL
           AND c.hop < 5
    )
    SELECT c.hop,
           f.food_id,
           f.pathyam_id,
           f.canonical_name_en,
           n.infoods_tagname,
           cv.value,
           n.unit,
           cv.confidence,
           s.source_key,
           s.citation,
           s.licence,
           s.is_commercial_cleared
      FROM chain c
      JOIN ref.composition_value cv ON cv.value_id = c.value_id
      JOIN ref.food_item  f ON f.food_id     = cv.food_id
      JOIN ref.nutrient   n ON n.nutrient_id = cv.nutrient_id
      JOIN ref.source     s ON s.source_id   = cv.source_id
     ORDER BY c.hop, n.display_order NULLS LAST, n.infoods_tagname;
$$;

-- --------------------------------------------------- serving unit resolver ----
-- Region-scoped resolution: "1 katori" in Kerala is not "1 katori" in Punjab.
-- Falls back up the region hierarchy, then to the unregioned default.
CREATE OR REPLACE FUNCTION ref.resolve_serving_grams(
    p_food_id   bigint,
    p_unit_id   bigint,
    p_region_id bigint DEFAULT NULL
)
RETURNS TABLE (grams numeric, grams_sd numeric, matched_region_id bigint)
LANGUAGE sql
STABLE
AS $$
    WITH RECURSIVE region_chain AS (
        SELECT r.region_id, r.parent_region_id, 0 AS distance
          FROM ref.region r WHERE r.region_id = p_region_id
        UNION ALL
        SELECT r.region_id, r.parent_region_id, rc.distance + 1
          FROM region_chain rc
          JOIN ref.region r ON r.region_id = rc.parent_region_id
    )
    SELECT fs.grams, fs.grams_sd, fs.region_id
      FROM ref.food_serving fs
      LEFT JOIN region_chain rc ON rc.region_id = fs.region_id
     WHERE fs.food_id = p_food_id
       AND fs.unit_id = p_unit_id
       AND (fs.region_id IS NULL OR rc.region_id IS NOT NULL)
     ORDER BY coalesce(rc.distance, 999), fs.is_default DESC
     LIMIT 1;
$$;

-- === End 008_functions.sql ===


-- === Begin 009_views_read_model.sql ===
-- ============================================================================
-- Pathyam · 009 · Views, compliance canaries, read model
-- ============================================================================

-- ------------------------------------------------- resolved composition ----
-- Every value with its source, licence and confidence attached. This is the shape
-- the API returns; provenance is not an optional expansion.
CREATE VIEW ref.v_composition_resolved AS
SELECT f.pathyam_id,
       f.canonical_name_en,
       f.food_group,
       n.infoods_tagname,
       n.name          AS nutrient_name,
       cv.value,
       n.unit,
       cv.sd,
       cv.n_samples,
       cv.basis,
       cv.confidence,
       cv.is_borrowed,
       cv.analytical_method,
       s.source_key,
       s.citation,
       s.licence,
       s.is_commercial_cleared
  FROM ref.composition_value cv
  JOIN ref.food_item f ON f.food_id     = cv.food_id
  JOIN ref.nutrient  n ON n.nutrient_id = cv.nutrient_id
  JOIN ref.source    s ON s.source_id   = cv.source_id
 WHERE cv.valid_to IS NULL;

-- ------------------------------------------------- COMPLIANCE CANARY #1 ----
-- Every nutrient value currently resting on a source that has NOT been cleared for
-- commercial use. Run this before every release. It should be empty, or every row
-- should have a permission_ref pending.
CREATE VIEW ref.v_uncleared_values AS
SELECT s.source_key,
       s.licence,
       s.permission_ref,
       count(*)                        AS value_count,
       count(DISTINCT cv.food_id)      AS food_count,
       min(cv.valid_from)              AS first_added
  FROM ref.composition_value cv
  JOIN ref.source s ON s.source_id = cv.source_id
 WHERE NOT s.is_commercial_cleared
   AND cv.valid_to IS NULL
 GROUP BY s.source_key, s.licence, s.permission_ref
 ORDER BY value_count DESC;

COMMENT ON VIEW ref.v_uncleared_values IS
'Release gate. Non-empty means the build contains values whose commercial reuse '
'rights are unestablished — principally ICMR-NIN IFCT until written permission lands.';

-- ------------------------------------------------- COMPLIANCE CANARY #2 ----
-- Confidence-tier composition of the dataset. Tier C/D creeping upward means the
-- database is drifting toward borrowed foreign values for Indian foods.
CREATE VIEW ref.v_confidence_profile AS
SELECT f.food_group,
       cv.confidence,
       count(*) AS value_count,
       round(100.0 * count(*) / sum(count(*)) OVER (PARTITION BY f.food_group), 1) AS pct_of_group
  FROM ref.composition_value cv
  JOIN ref.food_item f ON f.food_id = cv.food_id
 WHERE cv.valid_to IS NULL
 GROUP BY f.food_group, cv.confidence
 ORDER BY f.food_group, cv.confidence;

-- --------------------------------------------- TRAINING CONSENT GATE ----
-- Derived at query time. NEVER materialise this: a withdrawal must take effect on
-- the next query, not on the next pipeline run.
CREATE VIEW ml.v_training_eligible_user AS
SELECT u.user_id
  FROM app.app_user u
  JOIN app.user_consent   c ON c.user_id    = u.user_id
  JOIN app.consent_purpose p ON p.purpose_id = c.purpose_id
 WHERE p.purpose_key  = 'model_training'
   AND c.withdrawn_at IS NULL
   AND u.deleted_at   IS NULL
   AND NOT u.is_anonymised;

COMMENT ON VIEW ml.v_training_eligible_user IS
'DPDP gate. Every training-corpus query MUST join through this view. Materialising '
'it — or caching its result in a training job — reintroduces the compliance risk it exists to remove.';

-- The fine-tuning corpus. Photo logs, with what the model proposed, what the user
-- corrected it to, and which prior was active — from consenting users only.
CREATE VIEW ml.v_training_corpus AS
SELECT ml_.meal_log_id,
       mli.item_id,
       ml_.image_ref,
       ml_.image_sha256,
       ml_.region_id,
       mli.template_id,
       mli.param_bindings,
       mli.is_user_confirmed,
       ir.model_name,
       ir.model_version,
       ir.raw_output              AS vlm_output,
       uc.field                   AS corrected_field,
       uc.proposed_value,
       uc.corrected_value,
       ml_.logged_at
  FROM app.meal_log ml_
  JOIN ml.v_training_eligible_user te ON te.user_id = ml_.user_id   -- <<< consent gate
  JOIN app.meal_log_item mli ON mli.meal_log_id = ml_.meal_log_id
  LEFT JOIN ml.inference_run  ir ON ir.item_id = mli.item_id AND ir.stage = 'vlm'
  LEFT JOIN ml.user_correction uc ON uc.item_id = mli.item_id
 WHERE ml_.deleted_at IS NULL
   AND ml_.method = 'photo'
   AND ml_.image_ref IS NOT NULL;

-- -------------------------------------------------- calibration monitor ----
-- Does the stated 80% interval actually contain the corrected value 80% of the time?
-- A wide interval that is never wrong is not honest, merely vague. Track coverage.
CREATE VIEW ml.v_interval_calibration AS
SELECT date_trunc('week', uc.corrected_at)            AS week,
       count(*)                                       AS n_corrections,
       count(*) FILTER (
           WHERE (uc.corrected_value->>'energy_kcal')::numeric
                 BETWEEN mli.energy_kcal_p10 AND mli.energy_kcal_p90
       )                                              AS n_within_interval,
       round(100.0 * count(*) FILTER (
           WHERE (uc.corrected_value->>'energy_kcal')::numeric
                 BETWEEN mli.energy_kcal_p10 AND mli.energy_kcal_p90
       ) / nullif(count(*), 0), 1)                    AS pct_coverage
  FROM ml.user_correction uc
  JOIN app.meal_log_item mli ON mli.item_id = uc.item_id
 WHERE uc.corrected_value ? 'energy_kcal'
   AND mli.energy_kcal_p10 IS NOT NULL
 GROUP BY 1
 ORDER BY 1 DESC;

-- ------------------------------------------------------------ read model ----
-- Denormalised dish card: everything the app needs for one dish in a single row.
-- Refresh on dataset release, not on every write.
CREATE MATERIALIZED VIEW app.mv_dish_card AS
SELECT f.food_id,
       f.pathyam_id,
       f.canonical_name_en,
       f.food_group,
       f.density_g_per_ml,
       rt.template_id,
       rt.base_method,
       -- names in every language, for search and display
       ( SELECT jsonb_object_agg(x.lang, x.forms)
           FROM ( SELECT fn.lang,
                         jsonb_agg(DISTINCT coalesce(fn.name_native, fn.name_roman)) AS forms
                    FROM ref.food_name fn
                   WHERE fn.food_id = f.food_id
                   GROUP BY fn.lang) x )                        AS names,
       -- core nutrient panel, per 100 g
       ( SELECT jsonb_object_agg(n.infoods_tagname,
                jsonb_build_object('value', cv.value, 'unit', n.unit,
                                   'confidence', cv.confidence))
           FROM ref.composition_value cv
           JOIN ref.nutrient n ON n.nutrient_id = cv.nutrient_id
          WHERE cv.food_id = f.food_id
            AND cv.valid_to IS NULL
            AND cv.basis = 'per_100g'
            AND n.is_core )                                     AS core_nutrients,
       -- natural serving units
       ( SELECT jsonb_agg(jsonb_build_object(
                    'unit_key', su.unit_key, 'grams', fs.grams,
                    'grams_sd', fs.grams_sd, 'count_based', su.is_count_based))
           FROM ref.food_serving fs
           JOIN ref.serving_unit su ON su.unit_id = fs.unit_id
          WHERE fs.food_id = f.food_id )                        AS servings,
       ( SELECT jsonb_build_object('gi', gv.gi, 'gl', gv.gl_per_serving, 'pmid', gv.pmid)
           FROM ref.glycemic_value gv
          WHERE gv.food_id = f.food_id
          ORDER BY gv.confidence LIMIT 1 )                      AS glycemic,
       -- worst confidence tier present: drives whether clinical mode will show it.
       -- ORDER BY ... LIMIT 1 rather than max(): PostgreSQL defines comparison
       -- operators on enums but ships no max() aggregate for them.
       ( SELECT cv.confidence
           FROM ref.composition_value cv
          WHERE cv.food_id = f.food_id AND cv.valid_to IS NULL
          ORDER BY cv.confidence DESC LIMIT 1 )                  AS worst_confidence
  FROM ref.food_item f
  -- LATERAL + LIMIT 1: a food may have several active templates (regional variants);
  -- the dish card must stay one row per food or the unique index below fails.
  LEFT JOIN LATERAL (
        SELECT t.template_id, t.base_method
          FROM ref.recipe_template t
         WHERE t.food_id = f.food_id AND t.is_active
         ORDER BY t.version DESC, t.template_id
         LIMIT 1) rt ON true;

CREATE UNIQUE INDEX mv_dish_card_pk_idx ON app.mv_dish_card (food_id);
CREATE INDEX mv_dish_card_group_idx     ON app.mv_dish_card (food_group);

COMMENT ON MATERIALIZED VIEW app.mv_dish_card IS
'Read model. If Mongo or Redis is preferred at the serving layer, this view is what '
'you project into it — Postgres stays the system of record either way.';

-- === End 009_views_read_model.sql ===


-- === Begin 010_seed_example.sql ===
-- ============================================================================
-- Pathyam · 010 · Worked seed example — the dosa family
-- ============================================================================
-- A complete, runnable vertical slice: sources → nutrients → regions → ingredients
-- → composition → lexicon → parametric templates (incl. a nested sub-recipe) →
-- regional priors → serving units → GI → a consenting user → a photo log with a
-- full inference trace and a correction.
--
-- Nutrient values here are ILLUSTRATIVE PLACEHOLDERS of the right order of
-- magnitude, present so the schema and queries can be exercised. Real values load
-- from IFCT 2017 / INDB once the licences in the dossier are cleared.
-- ============================================================================

-- ---------------------------------------------------------------- sources ----
INSERT INTO ref.source (source_key, citation, doi_or_url, licence, is_commercial_cleared, notes) VALUES
 ('IFCT2017',
  'Longvah T, Ananthan R, Bhaskarachary K, Venkaiah K. Indian Food Composition Tables 2017. ICMR-NIN, Hyderabad.',
  'https://www.nin.res.in/ebooks/IFCT2017.pdf',
  'Not stated - ICMR copyright', false,
  'AWAITING WRITTEN PERMISSION. Deliberately left uncleared so it shows in ref.v_uncleared_values.'),
 ('INDB2024.11',
  'Vijayakumar A, Dubasi HB, Awasthi A, Jaacks LM. Development of an Indian Food Composition Database. Curr Dev Nutr. 2024;103790.',
  'https://www.anuvaad.org.in/indian-nutrient-databank/',
  'Described open access; no explicit licence file', false,
  'Awaiting written clarification from Anuvaad Solutions LLP.'),
 ('USDA-NRF-R6',
  'USDA Table of Nutrient Retention Factors, Release 6 (2007). USDA ARS.',
  'https://agdatacommons.nal.usda.gov/articles/dataset/24660888',
  'Public domain (US Government work)', true, NULL),
 ('USDA-FDC',
  'USDA FoodData Central. Agricultural Research Service.',
  'https://fdc.nal.usda.gov/', 'Public domain (US Government work)', true, NULL),
 ('COFID2021',
  'Composition of Foods Integrated Dataset (CoFID) 2021. Public Health England.',
  'https://www.gov.uk/government/publications/composition-of-foods-integrated-dataset-cofid',
  'Open Government Licence v3', true, NULL),
 ('PMID:35875218',
  'Shakappa D, Naik R, Sobhana PP. Glycemic carbohydrates, glycemic index and glycemic load of commonly consumed South Indian breakfast foods. J Food Sci Technol. 2022;59(9):3619-3626.',
  'https://doi.org/10.1007/s13197-022-05368-6',
  'Journal copyright; numerical findings citable as fact', true,
  'Authors are ICMR-NIN Dept of Dietetics - the same institute that holds IFCT.'),
 ('PATHYAM-EST',
  'Pathyam internal estimate pending analytical determination.',
  NULL, 'Proprietary', true, 'Tier D placeholder values.');

-- -------------------------------------------------------------- nutrients ----
INSERT INTO ref.nutrient (infoods_tagname, name, unit, decimals, nutrient_group, is_core, display_order) VALUES
 ('ENERC_KCAL', 'Energy',                'kcal', 0, 'proximate',    true,  1),
 ('PROCNT',     'Protein',               'g',    1, 'proximate',    true,  2),
 ('FAT',        'Total fat',             'g',    1, 'lipid',        true,  3),
 ('CHOAVLDF',   'Available carbohydrate','g',    1, 'carbohydrate', true,  4),
 ('FIBTG',      'Total dietary fibre',   'g',    1, 'carbohydrate', true,  5),
 ('FASAT',      'Saturated fatty acids', 'g',    2, 'fatty_acid',   false, 6),
 ('NA',         'Sodium',                'mg',   0, 'mineral',      true,  7),
 ('K',          'Potassium',             'mg',   0, 'mineral',      true,  8),
 ('P',          'Phosphorus',            'mg',   0, 'mineral',      false, 9),
 ('FE',         'Iron',                  'mg',   2, 'mineral',      false, 10),
 ('THIA',       'Thiamine',              'mg',   3, 'vitamin',      false, 11),
 ('FOLDFE',     'Folate, DFE',           'ug',   0, 'vitamin',      false, 12);

-- ---------------------------------------------------------------- regions ----
INSERT INTO ref.region (region_key, state, sub_region, iso_3166_2) VALUES
 ('TN',           'Tamil Nadu',    NULL,         'IN-TN'),
 ('KL',           'Kerala',        NULL,         'IN-KL'),
 ('KA',           'Karnataka',     NULL,         'IN-KA'),
 ('AP',           'Andhra Pradesh',NULL,         'IN-AP'),
 ('TG',           'Telangana',     NULL,         'IN-TG');

INSERT INTO ref.region (region_key, state, sub_region, parent_region_id, iso_3166_2)
SELECT 'TN-CHETTINAD', 'Tamil Nadu', 'Chettinad', region_id, 'IN-TN'
  FROM ref.region WHERE region_key = 'TN';
INSERT INTO ref.region (region_key, state, sub_region, parent_region_id, iso_3166_2)
SELECT 'KL-MALABAR', 'Kerala', 'Malabar', region_id, 'IN-KL'
  FROM ref.region WHERE region_key = 'KL';

-- ------------------------------------------------------------ food items ----
INSERT INTO ref.food_item
  (pathyam_id, canonical_name_en, scientific_name, food_group, is_recipe,
   ifct_code, density_g_per_ml, edible_portion_pct, foodon_iri)
VALUES
 ('PY-F-000001','Rice, parboiled, milled','Oryza sativa','cereal',  false,'A007',0.850,100,
  'http://purl.obolibrary.org/obo/FOODON_03301017'),
 ('PY-F-000002','Rice, raw, milled',      'Oryza sativa','cereal',  false,'A006',0.850,100,NULL),
 ('PY-F-000003','Black gram dhal, dehusked','Vigna mungo','pulse',  false,'B015',0.820,100,NULL),
 ('PY-F-000004','Fenugreek seeds',        'Trigonella foenum-graecum','spice',false,'S018',0.780,100,NULL),
 ('PY-F-000005','Gingelly (sesame) oil',  'Sesamum indicum','fat',   false,'V004',0.920,100,NULL),
 ('PY-F-000006','Coconut oil',            'Cocos nucifera','fat',    false,'V002',0.918,100,NULL),
 ('PY-F-000007','Ghee, cow',              NULL,           'fat',     false,'V009',0.911,100,NULL),
 ('PY-F-000008','Potato, boiled',         'Solanum tuberosum','vegetable',false,'D010',0.940,100,NULL),
 ('PY-F-000009','Onion, big',             'Allium cepa',  'vegetable',false,'D021',0.960,88,NULL),
 ('PY-F-000010','Salt, iodised',          NULL,           'condiment',false,NULL, 1.200,100,NULL),
 -- dishes (is_recipe = true)
 ('PY-F-000100','Dosa, plain',            NULL,'prepared_dish', true, NULL,0.720,100,NULL),
 ('PY-F-000101','Dosa, masala',           NULL,'prepared_dish', true, NULL,0.760,100,NULL),
 ('PY-F-000102','Masala dosa filling (potato masala)', NULL,'prepared_dish',true,NULL,0.880,100,NULL);

-- ------------------------------------------------- composition (per 100 g) ----
-- Illustrative values. Tier A = from IFCT; tier C = borrowed; tier D = estimate.
INSERT INTO ref.composition_value
  (food_id, nutrient_id, value, basis, sd, n_samples, confidence, source_id, analytical_method)
SELECT f.food_id, n.nutrient_id, v.value, 'per_100g', v.sd, v.n, v.tier::ref.confidence_tier,
       s.source_id, v.method
FROM (VALUES
  -- parboiled rice
  ('PY-F-000001','ENERC_KCAL', 346.0,  6.2, 18,'A','IFCT2017','bomb calorimetry / calculated'),
  ('PY-F-000001','PROCNT',       7.81, 0.42,18,'A','IFCT2017','Kjeldahl'),
  ('PY-F-000001','FAT',          0.52, 0.09,18,'A','IFCT2017','Soxhlet'),
  ('PY-F-000001','CHOAVLDF',    74.80, 1.10,18,'A','IFCT2017','difference'),
  ('PY-F-000001','FIBTG',        2.81, 0.31,18,'A','IFCT2017','AOAC 991.43'),
  ('PY-F-000001','K',          115.00,12.00,18,'A','IFCT2017','ICP-OES'),
  ('PY-F-000001','P',          125.00,10.00,18,'A','IFCT2017','ICP-OES'),
  ('PY-F-000001','THIA',         0.28, 0.03,18,'A','IFCT2017','HPLC'),
  ('PY-F-000001','NA',           2.00, 0.50,18,'A','IFCT2017','ICP-OES'),
  -- raw milled rice (lower thiamine - the parboiling difference)
  ('PY-F-000002','ENERC_KCAL', 356.0,  5.8, 18,'A','IFCT2017','calculated'),
  ('PY-F-000002','PROCNT',       7.94, 0.38,18,'A','IFCT2017','Kjeldahl'),
  ('PY-F-000002','FAT',          0.52, 0.08,18,'A','IFCT2017','Soxhlet'),
  ('PY-F-000002','CHOAVLDF',    78.20, 0.95,18,'A','IFCT2017','difference'),
  ('PY-F-000002','FIBTG',        2.02, 0.22,18,'A','IFCT2017','AOAC 991.43'),
  ('PY-F-000002','K',           90.00,10.00,18,'A','IFCT2017','ICP-OES'),
  ('PY-F-000002','THIA',         0.05, 0.01,18,'A','IFCT2017','HPLC'),
  ('PY-F-000002','NA',           2.00, 0.40,18,'A','IFCT2017','ICP-OES'),
  -- black gram dhal
  ('PY-F-000003','ENERC_KCAL', 341.0,  7.1, 12,'A','IFCT2017','calculated'),
  ('PY-F-000003','PROCNT',      23.02, 0.90,12,'A','IFCT2017','Kjeldahl'),
  ('PY-F-000003','FAT',          1.64, 0.18,12,'A','IFCT2017','Soxhlet'),
  ('PY-F-000003','CHOAVLDF',    52.10, 1.40,12,'A','IFCT2017','difference'),
  ('PY-F-000003','FIBTG',       15.60, 1.20,12,'A','IFCT2017','AOAC 991.43'),
  ('PY-F-000003','K',          983.00,45.00,12,'A','IFCT2017','ICP-OES'),
  ('PY-F-000003','P',          320.00,22.00,12,'A','IFCT2017','ICP-OES'),
  ('PY-F-000003','FE',           3.65, 0.40,12,'A','IFCT2017','ICP-OES'),
  ('PY-F-000003','FOLDFE',     216.00,18.00,12,'A','IFCT2017','microbiological'),
  ('PY-F-000003','NA',           8.00, 1.50,12,'A','IFCT2017','ICP-OES'),
  -- fenugreek
  ('PY-F-000004','ENERC_KCAL', 328.0,  9.0,  6,'A','IFCT2017','calculated'),
  ('PY-F-000004','PROCNT',      25.40, 1.10, 6,'A','IFCT2017','Kjeldahl'),
  ('PY-F-000004','FAT',          5.90, 0.60, 6,'A','IFCT2017','Soxhlet'),
  ('PY-F-000004','CHOAVLDF',    33.80, 2.00, 6,'A','IFCT2017','difference'),
  ('PY-F-000004','FIBTG',       48.00, 3.00, 6,'A','IFCT2017','AOAC 991.43'),
  ('PY-F-000004','K',          770.00,50.00, 6,'A','IFCT2017','ICP-OES'),
  ('PY-F-000004','NA',          67.00, 6.00, 6,'A','IFCT2017','ICP-OES'),
  -- fats
  ('PY-F-000005','ENERC_KCAL', 900.0,  0.0,  6,'A','IFCT2017','calculated'),
  ('PY-F-000005','FAT',        100.00, 0.00, 6,'A','IFCT2017','gravimetric'),
  ('PY-F-000005','FASAT',       14.20, 0.60, 6,'A','IFCT2017','GC-FID'),
  ('PY-F-000005','NA',           0.00, 0.00, 6,'A','IFCT2017','ICP-OES'),
  ('PY-F-000006','ENERC_KCAL', 900.0,  0.0,  6,'A','IFCT2017','calculated'),
  ('PY-F-000006','FAT',        100.00, 0.00, 6,'A','IFCT2017','gravimetric'),
  ('PY-F-000006','FASAT',       87.50, 1.20, 6,'A','IFCT2017','GC-FID'),
  ('PY-F-000006','NA',           0.00, 0.00, 6,'A','IFCT2017','ICP-OES'),
  ('PY-F-000007','ENERC_KCAL', 900.0,  0.0,  6,'A','IFCT2017','calculated'),
  ('PY-F-000007','FAT',        100.00, 0.00, 6,'A','IFCT2017','gravimetric'),
  ('PY-F-000007','FASAT',       62.00, 1.80, 6,'A','IFCT2017','GC-FID'),
  ('PY-F-000007','NA',           0.00, 0.00, 6,'A','IFCT2017','ICP-OES'),
  -- vegetables
  ('PY-F-000008','ENERC_KCAL',  87.0,  3.0, 10,'A','IFCT2017','calculated'),
  ('PY-F-000008','PROCNT',       1.87, 0.20,10,'A','IFCT2017','Kjeldahl'),
  ('PY-F-000008','FAT',          0.10, 0.03,10,'A','IFCT2017','Soxhlet'),
  ('PY-F-000008','CHOAVLDF',    18.40, 0.90,10,'A','IFCT2017','difference'),
  ('PY-F-000008','FIBTG',        1.80, 0.20,10,'A','IFCT2017','AOAC 991.43'),
  ('PY-F-000008','K',          380.00,30.00,10,'A','IFCT2017','ICP-OES'),
  ('PY-F-000008','P',           57.00, 6.00,10,'A','IFCT2017','ICP-OES'),
  ('PY-F-000008','NA',           6.00, 1.00,10,'A','IFCT2017','ICP-OES'),
  ('PY-F-000009','ENERC_KCAL',  46.0,  2.5, 10,'A','IFCT2017','calculated'),
  ('PY-F-000009','PROCNT',       1.20, 0.15,10,'A','IFCT2017','Kjeldahl'),
  ('PY-F-000009','FAT',          0.10, 0.02,10,'A','IFCT2017','Soxhlet'),
  ('PY-F-000009','CHOAVLDF',     9.34, 0.60,10,'A','IFCT2017','difference'),
  ('PY-F-000009','FIBTG',        2.10, 0.25,10,'A','IFCT2017','AOAC 991.43'),
  ('PY-F-000009','K',          146.00,14.00,10,'A','IFCT2017','ICP-OES'),
  ('PY-F-000009','NA',           4.00, 1.00,10,'A','IFCT2017','ICP-OES'),
  ('PY-F-000010','NA',       38758.00, 0.00, 3,'C','COFID2021','borrowed - stoichiometric')
) AS v(pid, tag, value, sd, n, tier, src, method)
JOIN ref.food_item f ON f.pathyam_id = v.pid
JOIN ref.nutrient  n ON n.infoods_tagname = v.tag
JOIN ref.source    s ON s.source_key = v.src;

-- A genuine BORROWED value, to exercise the provenance chain and the tier-C rules.
-- Seed the parboiled-rice folate figure first...
INSERT INTO ref.composition_value
  (food_id, nutrient_id, value, basis, sd, n_samples, confidence, source_id, analytical_method)
SELECT f.food_id, n.nutrient_id, 8.00, 'per_100g', 1.20, 18, 'A', s.source_id, 'microbiological'
  FROM ref.food_item f, ref.nutrient n, ref.source s
 WHERE f.pathyam_id = 'PY-F-000001'
   AND n.infoods_tagname = 'FOLDFE'
   AND s.source_key = 'IFCT2017';

INSERT INTO ref.composition_value
  (food_id, nutrient_id, value, basis, confidence, source_id, is_borrowed,
   borrowed_from_food_id, analytical_method, notes)
SELECT raw.food_id, n.nutrient_id, 8.00, 'per_100g', 'C', s.source_id, true,
       par.food_id, 'borrowed - no analytical value for raw milled rice',
       'Flagged in the UI as estimated. Excluded from clinical outputs by default.'
  FROM ref.food_item raw, ref.food_item par, ref.nutrient n, ref.source s
 WHERE raw.pathyam_id = 'PY-F-000002'
   AND par.pathyam_id = 'PY-F-000001'
   AND n.infoods_tagname = 'FOLDFE'
   AND s.source_key = 'PATHYAM-EST';

-- ---------------------------------------------------------------- lexicon ----
INSERT INTO ref.food_name (food_id, lang, name_native, name_roman, is_primary)
SELECT f.food_id, v.lang, v.native, v.roman, v.prim
FROM (VALUES
 ('PY-F-000100','en',NULL,          'Dosa, plain',  true),
 ('PY-F-000100','ta','தோசை',        'dosai',        true),
 ('PY-F-000100','ta',NULL,          'thosai',       false),
 ('PY-F-000100','te','దోస',          'dosa',         true),
 ('PY-F-000100','ml','ദോശ',         'dosha',        true),
 ('PY-F-000100','kn','ದೋಸೆ',        'dose',         true),
 ('PY-F-000100','kn',NULL,          'dosey',        false),
 ('PY-F-000101','en',NULL,          'Masala dosa',  true),
 ('PY-F-000101','ta','மசாலா தோசை',  'masala dosai', true),
 ('PY-F-000101','kn','ಮಸಾಲೆ ದೋಸೆ',  'masale dose',  true),
 ('PY-F-000101','ml','മസാല ദോശ',    'masala dosha', true),
 ('PY-F-000101','te','మసాలా దోస',    'masala dosa',  true),
 ('PY-F-000001','en',NULL,          'Parboiled rice', true),
 ('PY-F-000001','ta','புழுங்கல் அரிசி','puzhungal arisi', true),
 ('PY-F-000001','ml','പുഴുങ്ങലരി',   'puzhungalari', true),
 ('PY-F-000003','en',NULL,          'Urad dal',     true),
 ('PY-F-000003','ta','உளுந்து',      'ulundhu',      true),
 ('PY-F-000003','kn','ಉದ್ದಿನ ಬೇಳೆ',  'uddina bele',  true),
 ('PY-F-000005','en',NULL,          'Gingelly oil', true),
 ('PY-F-000005','ta','நல்லெண்ணெய்',  'nallennai',    true)
) AS v(pid, lang, native, roman, prim)
JOIN ref.food_item f ON f.pathyam_id = v.pid;

-- ------------------------------------------- retention and yield factors ----
INSERT INTO ref.retention_factor (food_group, cooking_method, nutrient_id, pct_retained, source_id)
SELECT v.grp, v.method, n.nutrient_id, v.pct, s.source_id
FROM (VALUES
 ('cereal','griddled','THIA',   70.0),
 ('cereal','griddled','FOLDFE', 75.0),
 ('cereal','griddled','K',      95.0),
 ('pulse', 'griddled','THIA',   75.0),
 ('pulse', 'griddled','FOLDFE', 70.0),
 ('pulse', 'griddled','FE',     95.0),
 ('vegetable','boiled','K',     70.0),
 ('vegetable','boiled','THIA',  65.0),
 ('vegetable','boiled','FOLDFE',60.0)
) AS v(grp, method, tag, pct)
JOIN ref.nutrient n ON n.infoods_tagname = v.tag
JOIN ref.source   s ON s.source_key = 'USDA-NRF-R6';

INSERT INTO ref.yield_factor (food_group, cooking_method, factor, source_id)
SELECT v.grp, v.method, v.f, s.source_id
FROM (VALUES ('cereal','griddled',0.82),('vegetable','boiled',0.95),('pulse','griddled',0.85))
     AS v(grp, method, f)
JOIN ref.source s ON s.source_key = 'USDA-NRF-R6';

-- ============================================================================
-- PARAMETRIC TEMPLATES — the worked example
-- ============================================================================

INSERT INTO ref.recipe_template (pathyam_id, food_id, base_method, default_servings, yield_factor, source_id, notes)
SELECT 'PY-T-000100', f.food_id, 'griddled', 1, 0.8200, s.source_id,
       'Plain dosa. Batter is rice:urad ~3:1 by dry weight, fermented 8-16 h.'
  FROM ref.food_item f, ref.source s
 WHERE f.pathyam_id = 'PY-F-000100' AND s.source_key = 'INDB2024.11';

INSERT INTO ref.recipe_template (pathyam_id, food_id, base_method, default_servings, yield_factor, source_id, notes)
SELECT 'PY-T-000102', f.food_id, 'boiled', 1, 0.9500, s.source_id,
       'Potato masala filling. Used as a SUB-RECIPE inside masala dosa.'
  FROM ref.food_item f, ref.source s
 WHERE f.pathyam_id = 'PY-F-000102' AND s.source_key = 'INDB2024.11';

INSERT INTO ref.recipe_template (pathyam_id, food_id, base_method, default_servings, yield_factor, source_id, notes)
SELECT 'PY-T-000101', f.food_id, 'griddled', 1, 0.8400, s.source_id,
       'Masala dosa = plain dosa parameters + nested potato masala sub-recipe.'
  FROM ref.food_item f, ref.source s
 WHERE f.pathyam_id = 'PY-F-000101' AND s.source_key = 'INDB2024.11';

-- --------------------------------------------------- parameters: plain dosa ----
INSERT INTO ref.template_parameter
  (template_id, param_name, dtype, unit, prior_dist, prior_params,
   observable_from_image, elicitation_question, elicitation_options, display_order)
SELECT t.template_id, v.pname, v.dt::ref.param_dtype, v.unit,
       v.dist::ref.prior_dist, v.params::jsonb, v.obs, v.q, v.opts::jsonb, v.ord
FROM (VALUES
 ('batter_g','continuous','g','lognormal','{"mu": 4.50, "sigma": 0.25}', true,
  NULL, NULL, 1),
 ('rice_fraction','continuous','ratio','normal','{"mu": 0.75, "sigma": 0.04}', false,
  NULL, NULL, 2),
 -- fat_g is unobservable and dominates energy variance. This is the one question to ask.
 ('fat_g','continuous','g','lognormal','{"mu": 2.08, "sigma": 0.55}', false,
  'How much oil or ghee was used?',
  '[{"label":"little","value":4},{"label":"medium","value":8},{"label":"generous","value":15}]', 3),
 ('fat_type','categorical',NULL,'categorical',
  '{"categories": ["gingelly","coconut","ghee","sunflower"], "weights": [0.45,0.30,0.15,0.10]}',
  false, 'Which cooking fat?',
  '[{"label":"Gingelly","value":"gingelly"},{"label":"Coconut","value":"coconut"},{"label":"Ghee","value":"ghee"}]', 4),
 ('rice_type','categorical',NULL,'categorical',
  '{"categories": ["parboiled","raw"], "weights": [0.70,0.30]}', false, NULL, NULL, 5),
 ('fermentation_h','continuous','h','normal','{"mu": 12, "sigma": 3}', false, NULL, NULL, 6)
) AS v(pname, dt, unit, dist, params, obs, q, opts, ord)
JOIN ref.recipe_template t ON t.pathyam_id = 'PY-T-000100';

-- ------------------------------------------------ parameters: potato masala ----
INSERT INTO ref.template_parameter
  (template_id, param_name, dtype, unit, prior_dist, prior_params, observable_from_image, display_order)
SELECT t.template_id, v.pname, v.dt::ref.param_dtype, v.unit,
       v.dist::ref.prior_dist, v.params::jsonb, v.obs, v.ord
FROM (VALUES
 ('potato_g','continuous','g','lognormal','{"mu": 4.25, "sigma": 0.30}', true,  1),
 ('onion_g', 'continuous','g','lognormal','{"mu": 3.00, "sigma": 0.35}', true,  2),
 ('masala_fat_g','continuous','g','lognormal','{"mu": 1.61, "sigma": 0.45}', false, 3)
) AS v(pname, dt, unit, dist, params, obs, ord)
JOIN ref.recipe_template t ON t.pathyam_id = 'PY-T-000102';

-- ------------------------------------------------ parameters: masala dosa ----
INSERT INTO ref.template_parameter
  (template_id, param_name, dtype, unit, prior_dist, prior_params,
   observable_from_image, elicitation_question, elicitation_options, display_order)
SELECT t.template_id, v.pname, v.dt::ref.param_dtype, v.unit,
       v.dist::ref.prior_dist, v.params::jsonb, v.obs, v.q, v.opts::jsonb, v.ord
FROM (VALUES
 ('batter_g','continuous','g','lognormal','{"mu": 4.62, "sigma": 0.25}', true, NULL, NULL, 1),
 ('rice_fraction','continuous','ratio','normal','{"mu": 0.75, "sigma": 0.04}', false, NULL, NULL, 2),
 ('fat_g','continuous','g','lognormal','{"mu": 2.30, "sigma": 0.55}', false,
  'How much oil or ghee was used?',
  '[{"label":"little","value":6},{"label":"medium","value":11},{"label":"generous","value":20}]', 3),
 ('fat_type','categorical',NULL,'categorical',
  '{"categories": ["gingelly","coconut","ghee","sunflower"], "weights": [0.40,0.25,0.25,0.10]}',
  false, NULL, NULL, 4),
 ('rice_type','categorical',NULL,'categorical',
  '{"categories": ["parboiled","raw"], "weights": [0.70,0.30]}', false, NULL, NULL, 5),
 ('filling_g','continuous','g','lognormal','{"mu": 4.25, "sigma": 0.30}', true, NULL, NULL, 6)
) AS v(pname, dt, unit, dist, params, obs, q, opts, ord)
JOIN ref.recipe_template t ON t.pathyam_id = 'PY-T-000101';

-- --------------------------------------------------------- ingredient lists ----
-- Plain dosa: quantity expressions over the declared parameters.
INSERT INTO ref.template_ingredient
  (template_id, food_id, qty_expr, unit, preparation_state, cooking_method, display_order)
SELECT t.template_id, f.food_id, v.expr, 'g', v.prep, 'griddled', v.ord
FROM (VALUES
 ('PY-F-000001','batter_g * rice_fraction * (rice_type == "parboiled")','soaked_ground',1),
 ('PY-F-000002','batter_g * rice_fraction * (rice_type == "raw")',      'soaked_ground',2),
 ('PY-F-000003','batter_g * (1 - rice_fraction) * 0.95','soaked_ground',3),
 ('PY-F-000004','batter_g * (1 - rice_fraction) * 0.05','soaked_ground',4),
 ('PY-F-000005','fat_g * (fat_type == "gingelly")','raw',5),
 ('PY-F-000006','fat_g * (fat_type == "coconut")', 'raw',6),
 ('PY-F-000007','fat_g * (fat_type == "ghee")',    'raw',7),
 ('PY-F-000010','batter_g * 0.012','raw',8)
) AS v(pid, expr, prep, ord)
JOIN ref.food_item f ON f.pathyam_id = v.pid
JOIN ref.recipe_template t ON t.pathyam_id = 'PY-T-000100';

-- Potato masala sub-recipe.
INSERT INTO ref.template_ingredient
  (template_id, food_id, qty_expr, unit, preparation_state, cooking_method, display_order)
SELECT t.template_id, f.food_id, v.expr, 'g', v.prep, 'boiled', v.ord
FROM (VALUES
 ('PY-F-000008','potato_g','boiled',1),
 ('PY-F-000009','onion_g','raw',2),
 ('PY-F-000005','masala_fat_g','raw',3),
 ('PY-F-000010','(potato_g + onion_g) * 0.010','raw',4)
) AS v(pid, expr, prep, ord)
JOIN ref.food_item f ON f.pathyam_id = v.pid
JOIN ref.recipe_template t ON t.pathyam_id = 'PY-T-000102';

-- Masala dosa: batter ingredients PLUS a nested sub-recipe reference.
INSERT INTO ref.template_ingredient
  (template_id, food_id, qty_expr, unit, preparation_state, cooking_method, display_order)
SELECT t.template_id, f.food_id, v.expr, 'g', v.prep, 'griddled', v.ord
FROM (VALUES
 ('PY-F-000001','batter_g * rice_fraction * (rice_type == "parboiled")','soaked_ground',1),
 ('PY-F-000002','batter_g * rice_fraction * (rice_type == "raw")',      'soaked_ground',2),
 ('PY-F-000003','batter_g * (1 - rice_fraction) * 0.95','soaked_ground',3),
 ('PY-F-000005','fat_g * (fat_type == "gingelly")','raw',4),
 ('PY-F-000006','fat_g * (fat_type == "coconut")', 'raw',5),
 ('PY-F-000007','fat_g * (fat_type == "ghee")',    'raw',6)
) AS v(pid, expr, prep, ord)
JOIN ref.food_item f ON f.pathyam_id = v.pid
JOIN ref.recipe_template t ON t.pathyam_id = 'PY-T-000101';

-- THE NESTED SUB-RECIPE. filling_g scales the whole potato-masala template.
INSERT INTO ref.template_ingredient
  (template_id, sub_template_id, qty_expr, unit, display_order)
SELECT parent.template_id, child.template_id, 'filling_g', 'g', 7
  FROM ref.recipe_template parent, ref.recipe_template child
 WHERE parent.pathyam_id = 'PY-T-000101' AND child.pathyam_id = 'PY-T-000102';

-- -------------------------------------------------------- regional priors ----
-- Kerala is not 288 extra rows. It is a shifted prior.
INSERT INTO ref.regional_prior (template_id, region_id, param_name, prior_dist, prior_params, notes)
SELECT t.template_id, r.region_id, v.pname, v.dist::ref.prior_dist, v.params::jsonb, v.note
FROM (VALUES
 ('KL','fat_type','categorical',
  '{"categories": ["gingelly","coconut","ghee","sunflower"], "weights": [0.10,0.75,0.10,0.05]}',
  'Kerala: coconut oil dominant - materially different fatty acid profile.'),
 ('KL','rice_type','categorical','{"categories": ["parboiled","raw"], "weights": [0.92,0.08]}',
  'Kerala: parboiled rice near-universal; affects thiamine retention and GI.'),
 ('TN','fat_type','categorical',
  '{"categories": ["gingelly","coconut","ghee","sunflower"], "weights": [0.60,0.15,0.15,0.10]}',
  'Tamil Nadu: gingelly (sesame) oil dominant.'),
 ('TN','rice_type','categorical','{"categories": ["parboiled","raw"], "weights": [0.80,0.20]}', NULL),
 ('KA','fat_type','categorical',
  '{"categories": ["gingelly","coconut","ghee","sunflower"], "weights": [0.25,0.15,0.30,0.30]}',
  'Karnataka: ghee and sunflower more common; Bangalore masala dosa is ghee-heavy.'),
 ('KA','rice_type','categorical','{"categories": ["parboiled","raw"], "weights": [0.45,0.55]}', NULL)
) AS v(rkey, pname, dist, params, note)
JOIN ref.region r ON r.region_key = v.rkey
JOIN ref.recipe_template t ON t.pathyam_id = 'PY-T-000100';

-- ---------------------------------------------------------- serving units ----
INSERT INTO ref.serving_unit (unit_key, name_en, name_i18n, volume_ml, typical_g, typical_g_sd, applies_to_group, is_count_based, region_id)
SELECT v.k, v.n, v.i18n::jsonb, v.ml, v.g, v.sd, v.grp, v.cnt, r.region_id
FROM (VALUES
 ('katori_south','Katori (South Indian)','{"ta":"கிண்ணம்","ml":"കിണ്ണം","kn":"ಬಟ್ಟಲು","te":"గిన్నె"}',
  130.0, NULL, NULL, NULL, false, 'TN'),
 ('katori_generic','Katori (survey standard 150 ml)','{}', 150.0, NULL, NULL, NULL, false, NULL),
 ('dosa_medium','Dosa (medium)','{"ta":"தோசை","kn":"ದೋಸೆ"}', NULL, 85.0, 22.0,'prepared_dish', true, NULL),
 ('dosa_large','Dosa (large / paper)','{}', NULL, 120.0, 30.0,'prepared_dish', true, NULL),
 ('idli_piece','Idli (1 piece)','{"ta":"இட்லி"}', NULL, 45.0, 9.0, 'prepared_dish', true, NULL),
 ('tumbler','Tumbler','{"ta":"டம்ளர்"}', 180.0, NULL, NULL, NULL, false, NULL)
) AS v(k, n, i18n, ml, g, sd, grp, cnt, rkey)
LEFT JOIN ref.region r ON r.region_key = v.rkey;

INSERT INTO ref.food_serving (food_id, unit_id, grams, grams_sd, region_id, is_default, source_id)
SELECT f.food_id, u.unit_id, v.g, v.sd, r.region_id, v.def, s.source_id
FROM (VALUES
 ('PY-F-000100','dosa_medium', 85.0, 22.0, NULL, true),
 ('PY-F-000100','dosa_large', 120.0, 30.0, NULL, false),
 ('PY-F-000101','dosa_medium',160.0, 38.0, NULL, true),
 ('PY-F-000101','dosa_large', 215.0, 48.0, 'KA', false)
) AS v(pid, ukey, g, sd, rkey, def)
JOIN ref.food_item f ON f.pathyam_id = v.pid
JOIN ref.serving_unit u ON u.unit_key = v.ukey
LEFT JOIN ref.region r ON r.region_key = v.rkey
JOIN ref.source s ON s.source_key = 'PATHYAM-EST';

-- ----------------------------------------------------------- glycemic data ----
INSERT INTO ref.glycemic_value
  (food_id, gi, gi_sem, gi_reference, gl_per_serving, n_subjects, population, method, pmid, source_id, confidence)
SELECT f.food_id, v.gi, v.sem, 'glucose', v.gl, 10, 'healthy adults, South India',
       'FAO/WHO 1998', '35875218', s.source_id, 'A'
FROM (VALUES
 ('PY-F-000100', 76.30, 3.10, 39.69),   -- plain dosa: highest GL in the study
 ('PY-F-000101', 71.40, 2.80, 34.20)
) AS v(pid, gi, sem, gl)
JOIN ref.food_item f ON f.pathyam_id = v.pid
JOIN ref.source s ON s.source_key = 'PMID:35875218';

-- ============================================================================
-- A CONSENTING USER, A PHOTO LOG, AND A FULL INFERENCE TRACE
-- ============================================================================

INSERT INTO app.app_user (user_id, region_id, preferred_lang, year_of_birth, sex_at_birth)
SELECT '11111111-1111-1111-1111-111111111111', region_id, 'ta', 1979, 'male'
  FROM ref.region WHERE region_key = 'TN';

-- Core service consent (required) + model training consent (separate and optional).
INSERT INTO app.user_consent (user_id, purpose_id, notice_version, notice_lang, evidence)
SELECT '11111111-1111-1111-1111-111111111111', p.purpose_id, 'notice-v1.2', 'ta',
       jsonb_build_object('ui','onboarding_step_3','ts', now()::text, 'method','explicit_toggle')
  FROM app.consent_purpose p
 WHERE p.purpose_key IN ('core_service','model_training');

INSERT INTO app.meal_log (meal_log_id, user_id, meal_slot, method, region_id, image_ref, image_sha256)
SELECT '22222222-2222-2222-2222-222222222222',
       '11111111-1111-1111-1111-111111111111',
       'breakfast', 'photo', region_id,
       's3://pathyam-media/2026/08/12/abc123.jpg',
       decode('a1b2c3d4','hex')
  FROM ref.region WHERE region_key = 'TN';

INSERT INTO app.meal_log_item
  (item_id, meal_log_id, template_id, food_id, param_bindings, servings, unit_id,
   energy_kcal_p50, energy_kcal_p10, energy_kcal_p90, nutrients,
   dominant_uncertainty_param, engine_version, computed_at, is_user_confirmed, confirmed_at)
SELECT '33333333-3333-3333-3333-333333333333',
       '22222222-2222-2222-2222-222222222222',
       t.template_id, f.food_id,
       '{"batter_g": 101.5, "rice_fraction": 0.75, "fat_g": 11.0, "fat_type": "ghee", "rice_type": "parboiled", "filling_g": 72.0}'::jsonb,
       1, u.unit_id,
       385.00, 291.00, 518.00,
       '{"CHOAVLDF": {"p10":44.1,"p50":52.3,"p90":61.4},
         "PROCNT":   {"p10":7.2, "p50":8.6, "p90":10.1},
         "FAT":      {"p10":6.4, "p50":12.1,"p90":21.8},
         "K":        {"p10":410, "p50":498, "p90":602}}'::jsonb,
       'fat_g', 'engine-0.3.1', now(), true, now()
  FROM ref.recipe_template t
  JOIN ref.food_item f ON f.pathyam_id = 'PY-F-000101'
  JOIN ref.serving_unit u ON u.unit_key = 'dosa_medium'
 WHERE t.pathyam_id = 'PY-T-000101';

-- The inference trace: what the VLM saw, what retrieval proposed, what won.
INSERT INTO ml.inference_run (run_id, item_id, stage, model_name, model_version, prompt_version, raw_output, latency_ms)
VALUES
 ('44444444-4444-4444-4444-444444444444','33333333-3333-3333-3333-333333333333',
  'vlm','claude-vision','2026-06','dish-extract-v4',
  '{"dish_candidates":[{"name":"masala dosa","confidence":0.81},
                       {"name":"plain dosa","confidence":0.12},
                       {"name":"ghee roast","confidence":0.05}],
    "visible_ingredients":["potato","onion","curry leaf"],
    "vessel":"steel plate","count":1,
    "attributes":{"browning":"high","oil_sheen":"high","folded":true},
    "reference_objects":["steel tumbler"]}'::jsonb, 1180),
 ('55555555-5555-5555-5555-555555555555','33333333-3333-3333-3333-333333333333',
  'retrieval','bge-m3-indic','v1.2',NULL,'{"k":10,"index":"dish_ontology_v3"}'::jsonb, 24);

INSERT INTO ml.inference_candidate (run_id, rank, template_id, score)
SELECT '55555555-5555-5555-5555-555555555555', v.rank, t.template_id, v.score
FROM (VALUES (1,'PY-T-000101',0.902341),(2,'PY-T-000100',0.64312),(3,'PY-T-000102',0.318772))
     AS v(rank, tpl, score)
JOIN ref.recipe_template t ON t.pathyam_id = v.tpl;

-- Parameter estimates with provenance. Note fat_g arrived from THREE sources;
-- user_stated won the precedence contest.
INSERT INTO ml.parameter_estimate (item_id, param_name, source, dist, dist_params, was_used)
VALUES
 ('33333333-3333-3333-3333-333333333333','batter_g','vlm','normal','{"mu":101.5,"sigma":18.0}',true),
 ('33333333-3333-3333-3333-333333333333','fat_g','population_prior','lognormal','{"mu":2.30,"sigma":0.55}',false),
 ('33333333-3333-3333-3333-333333333333','fat_g','regional_prior','lognormal','{"mu":2.35,"sigma":0.50}',false),
 ('33333333-3333-3333-3333-333333333333','fat_g','user_stated','point','{"value":11.0}',true),
 ('33333333-3333-3333-3333-333333333333','fat_type','regional_prior','categorical',
  '{"categories":["gingelly","coconut","ghee","sunflower"],"weights":[0.60,0.15,0.15,0.10]}',false),
 ('33333333-3333-3333-3333-333333333333','fat_type','user_stated','point','{"value":"ghee"}',true);

-- The correction: proposed + corrected + which run produced the proposal.
INSERT INTO ml.user_correction (item_id, field, proposed_value, corrected_value, run_id)
VALUES
 ('33333333-3333-3333-3333-333333333333','param:fat_type',
  '{"value":"gingelly"}','{"value":"ghee"}','44444444-4444-4444-4444-444444444444'),
 ('33333333-3333-3333-3333-333333333333','param:fat_g',
  '{"value":9.8}','{"value":11.0,"energy_kcal":396}','44444444-4444-4444-4444-444444444444');

-- The learned personal prior, after this correction.
INSERT INTO app.user_parameter_prior (user_id, template_id, param_name, dist, dist_params, n_observations)
SELECT '11111111-1111-1111-1111-111111111111', t.template_id, 'fat_g',
       'lognormal', '{"mu": 2.36, "sigma": 0.31}'::jsonb, 7
  FROM ref.recipe_template t WHERE t.pathyam_id = 'PY-T-000101';

REFRESH MATERIALIZED VIEW app.mv_dish_card;

-- === End 010_seed_example.sql ===


-- === Begin 011_trgm_indexes_optional.sql ===
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

-- === End 011_trgm_indexes_optional.sql ===


-- === Begin 012_recipe_compiler_state_machine.sql ===
-- ============================================================================
-- Pathyam · 012 · Recipe Compiler State Machine
-- ============================================================================
-- Adds explicit compilation states and tracking for parametric recipe templates.
--
-- Principle: Recipes store parameters, AST expressions, and provenance references.
-- They NEVER store static calculated nutrient numbers directly.
--
-- States:
--   DRAFT                Initial authored template
--   RESOLUTION_REQUIRED  One or more ingredient text terms unmapped to food_id
--   MISSING_COMPOSITION  Canonical food_id lacks core IFCT nutrient composition
--   MISSING_QUANTITY     Invalid or incomplete parameter quantity expression
--   QC_FAILED            Failed energy density, yield, or retention validation
--   COMPUTABLE           100% resolved, valid AST, complete composition & QC pass
--   VALIDATED            Clinically reviewed and approved by a dietitian
-- ============================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'recipe_state') THEN
        CREATE TYPE ref.recipe_state AS ENUM (
            'DRAFT',
            'RESOLUTION_REQUIRED',
            'MISSING_COMPOSITION',
            'MISSING_QUANTITY',
            'QC_FAILED',
            'COMPUTABLE',
            'VALIDATED'
        );
    END IF;
END $$;

-- Add status column to recipe_template if not present
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_schema = 'ref' 
          AND table_name = 'recipe_template' 
          AND column_name = 'status'
    ) THEN
        ALTER TABLE ref.recipe_template 
        ADD COLUMN status ref.recipe_state NOT NULL DEFAULT 'DRAFT';
    END IF;
END $$;

-- View for recipe compiler state audit
CREATE OR REPLACE VIEW ref.v_recipe_compiler_status AS
SELECT 
    t.template_id,
    t.pathyam_id,
    f.canonical_name_en AS dish_name,
    t.base_method,
    t.status,
    t.yield_factor,
    COUNT(DISTINCT ti.template_ingredient_id) AS total_ingredients,
    COUNT(DISTINCT CASE WHEN ti.food_id IS NOT NULL THEN ti.template_ingredient_id END) AS resolved_foods,
    COUNT(DISTINCT CASE WHEN ti.sub_template_id IS NOT NULL THEN ti.template_ingredient_id END) AS nested_sub_recipes
FROM ref.recipe_template t
JOIN ref.food_item f ON f.food_id = t.food_id
LEFT JOIN ref.template_ingredient ti ON ti.template_id = t.template_id
GROUP BY t.template_id, t.pathyam_id, f.canonical_name_en, t.base_method, t.status, t.yield_factor;

-- === End 012_recipe_compiler_state_machine.sql ===
