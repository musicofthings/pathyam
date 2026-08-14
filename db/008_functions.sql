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
