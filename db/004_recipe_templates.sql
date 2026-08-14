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
