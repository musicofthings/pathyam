-- ============================================================================
-- Pathyam · Demonstration queries
-- Each block exercises one of the query patterns the architecture document
-- argued does not require a graph database.
-- ============================================================================

\echo '=== Q1 · Recipe expansion, including a nested sub-recipe (depth 2) ==='
-- Masala dosa contains the potato-masala template. Note how expr_path carries the
-- chain of expressions: the engine multiplies them against a sampled parameter vector.
SELECT depth, leaf_pathyam_id, leaf_name_en, expr_path, cooking_method
  FROM ref.expand_template(
        (SELECT template_id FROM ref.recipe_template WHERE pathyam_id = 'PY-T-000101'));

\echo ''
\echo '=== Q2 · Cycle guard: a template must not contain itself, at any depth ==='
DO $$
BEGIN
    INSERT INTO ref.template_ingredient (template_id, sub_template_id, qty_expr)
    SELECT child.template_id, parent.template_id, '1.0'
      FROM ref.recipe_template parent, ref.recipe_template child
     WHERE parent.pathyam_id = 'PY-T-000101' AND child.pathyam_id = 'PY-T-000102';
    RAISE NOTICE 'FAIL: cycle was allowed';
EXCEPTION WHEN check_violation THEN
    RAISE NOTICE 'PASS: cycle rejected - %', SQLERRM;
END $$;

\echo ''
\echo '=== Q3 · Provenance chain: where did this number come from? ==='
SELECT hop, food_name, nutrient_tag, value, unit, confidence, source_key, commercial_ok
  FROM ref.provenance_chain((SELECT food_id FROM ref.food_item WHERE pathyam_id = 'PY-F-000002'))
 WHERE nutrient_tag = 'FOLDFE';

\echo ''
\echo '=== Q4 · COMPLIANCE CANARY: values resting on uncleared sources ==='
-- Should be non-empty until the ICMR-NIN and Anuvaad permissions land.
SELECT * FROM ref.v_uncleared_values;

\echo ''
\echo '=== Q5 · Confidence profile of the dataset ==='
SELECT * FROM ref.v_confidence_profile;

\echo ''
\echo '=== Q6 · Clinical constraint filter (the query a graph DB is bad at) ==='
-- "Ingredients under 200 mg potassium per 100 g" - the CKD use case.
SELECT f.canonical_name_en, f.food_group,
       max(cv.value) FILTER (WHERE n.infoods_tagname = 'K')          AS potassium_mg,
       max(cv.value) FILTER (WHERE n.infoods_tagname = 'ENERC_KCAL') AS energy_kcal,
       max(cv.confidence::text) FILTER (WHERE n.infoods_tagname = 'K') AS k_confidence
  FROM ref.food_item f
  JOIN ref.composition_value cv ON cv.food_id = f.food_id AND cv.valid_to IS NULL
  JOIN ref.nutrient n ON n.nutrient_id = cv.nutrient_id
 WHERE NOT f.is_recipe
 GROUP BY f.food_id, f.canonical_name_en, f.food_group
HAVING max(cv.value) FILTER (WHERE n.infoods_tagname = 'K') < 200
 ORDER BY potassium_mg;

\echo ''
\echo '=== Q7 · Regional priors: how Kerala and Tamil Nadu differ for one template ==='
SELECT r.region_key, rp.param_name, rp.prior_params
  FROM ref.regional_prior rp
  JOIN ref.region r ON r.region_id = rp.region_id
  JOIN ref.recipe_template t ON t.template_id = rp.template_id
 WHERE t.pathyam_id = 'PY-T-000100' AND rp.param_name = 'fat_type'
 ORDER BY r.region_key;

\echo ''
\echo '=== Q8 · Parameters the vision model CANNOT see (so the app must ask) ==='
SELECT tp.param_name, tp.dtype, tp.prior_dist, tp.elicitation_question
  FROM ref.template_parameter tp
  JOIN ref.recipe_template t ON t.template_id = tp.template_id
 WHERE t.pathyam_id = 'PY-T-000101' AND NOT tp.observable_from_image
 ORDER BY tp.display_order;

\echo ''
\echo '=== Q9 · Parameter provenance for a logged item: which source won? ==='
SELECT param_name, source, dist, dist_params, was_used
  FROM ml.parameter_estimate
 WHERE item_id = '33333333-3333-3333-3333-333333333333'
 ORDER BY param_name, was_used DESC;

\echo ''
\echo '=== Q10 · Region-scoped serving resolution (1 katori is not a constant) ==='
SELECT 'masala dosa, large, in Karnataka' AS scenario, *
  FROM ref.resolve_serving_grams(
        (SELECT food_id FROM ref.food_item   WHERE pathyam_id = 'PY-F-000101'),
        (SELECT unit_id FROM ref.serving_unit WHERE unit_key  = 'dosa_large'),
        (SELECT region_id FROM ref.region     WHERE region_key = 'KA'));

\echo ''
\echo '=== Q11 · TRAINING CORPUS, before consent withdrawal ==='
SELECT count(*) AS rows_available FROM ml.v_training_corpus;

\echo ''
\echo '=== Q12 · Withdraw model_training consent, then re-check ==='
UPDATE app.user_consent c
   SET withdrawn_at = now()
  FROM app.consent_purpose p
 WHERE p.purpose_id = c.purpose_id
   AND p.purpose_key = 'model_training'
   AND c.user_id = '11111111-1111-1111-1111-111111111111';

SELECT count(*) AS rows_available_after_withdrawal FROM ml.v_training_corpus;
\echo '(Expect 0. The view derives eligibility at query time, so withdrawal is'
\echo ' effective immediately rather than at the next pipeline run.)'

-- restore, so the script is idempotent across runs
UPDATE app.user_consent c
   SET withdrawn_at = NULL
  FROM app.consent_purpose p
 WHERE p.purpose_id = c.purpose_id
   AND p.purpose_key = 'model_training'
   AND c.user_id = '11111111-1111-1111-1111-111111111111';

\echo ''
\echo '=== Q13 · The dish card (read model) ==='
-- NOTE the deliberate asymmetry below, which is a design property and not a gap:
--   * an INGREDIENT (rice) has stored core_nutrients and a confidence tier
--   * a RECIPE DISH (masala dosa) has names, servings and GI but NO stored nutrients
-- Recipe nutrition is COMPUTED from the template against a sampled parameter vector
-- at request time. Storing it would freeze one parameter binding and silently
-- discard the uncertainty that is the whole point of the design.
SELECT pathyam_id,
       canonical_name_en,
       CASE WHEN template_id IS NOT NULL THEN 'computed from template'
            ELSE 'stored composition' END          AS nutrition_source,
       (SELECT count(*) FROM jsonb_object_keys(coalesce(names,'{}'))) AS n_languages,
       core_nutrients IS NOT NULL                  AS has_stored_nutrients,
       jsonb_array_length(coalesce(servings,'[]')) AS n_serving_units,
       glycemic->>'gi'                             AS gi,
       worst_confidence
  FROM app.mv_dish_card
 WHERE pathyam_id IN ('PY-F-000101','PY-F-000001')
 ORDER BY pathyam_id;

\echo ''
\echo '--- full card for masala dosa ---'
SELECT jsonb_pretty(names) AS names, jsonb_pretty(servings) AS servings,
       jsonb_pretty(glycemic) AS glycemic
  FROM app.mv_dish_card WHERE pathyam_id = 'PY-F-000101';

\echo ''
\echo '=== Q14 · Constraint enforcement: a tier-A value cannot be borrowed ==='
DO $$
BEGIN
    INSERT INTO ref.composition_value
      (food_id, nutrient_id, value, confidence, source_id, is_borrowed, borrowed_from_food_id)
    SELECT (SELECT food_id FROM ref.food_item WHERE pathyam_id='PY-F-000002'),
           (SELECT nutrient_id FROM ref.nutrient WHERE infoods_tagname='FE'),
           1.0, 'A',
           (SELECT source_id FROM ref.source WHERE source_key='IFCT2017'),
           true,
           (SELECT food_id FROM ref.food_item WHERE pathyam_id='PY-F-000001');
    RAISE NOTICE 'FAIL: borrowed tier-A value was allowed';
EXCEPTION WHEN check_violation THEN
    RAISE NOTICE 'PASS: borrowed value cannot claim tier A';
END $$;

\echo ''
\echo '=== Q15 · Constraint enforcement: malformed prior payload is rejected ==='
DO $$
BEGIN
    INSERT INTO ref.template_parameter (template_id, param_name, dtype, prior_dist, prior_params)
    SELECT template_id, 'bad_param', 'continuous', 'lognormal', '{"mean": 5}'::jsonb
      FROM ref.recipe_template WHERE pathyam_id = 'PY-T-000100';
    RAISE NOTICE 'FAIL: malformed prior accepted';
EXCEPTION WHEN check_violation THEN
    RAISE NOTICE 'PASS: lognormal prior requires mu and sigma';
END $$;

\echo ''
\echo '=== Q16 · Interval ordering is enforced (p10 <= p50 <= p90) ==='
DO $$
BEGIN
    UPDATE app.meal_log_item SET energy_kcal_p10 = 999
     WHERE item_id = '33333333-3333-3333-3333-333333333333';
    RAISE NOTICE 'FAIL: inverted interval accepted';
EXCEPTION WHEN check_violation THEN
    RAISE NOTICE 'PASS: inverted credible interval rejected';
END $$;
