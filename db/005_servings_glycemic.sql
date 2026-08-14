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
