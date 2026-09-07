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
