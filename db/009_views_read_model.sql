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
