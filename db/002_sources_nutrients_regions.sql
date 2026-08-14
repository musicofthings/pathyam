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
