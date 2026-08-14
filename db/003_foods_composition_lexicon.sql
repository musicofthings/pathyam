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
