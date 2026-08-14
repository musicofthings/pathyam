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
