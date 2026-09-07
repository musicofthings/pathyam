-- ============================================================================
-- Pathyam · 013 · Meal log application fields
-- ============================================================================
-- The journal moved out of an in-process Python list and onto app.meal_log.
-- Two columns are needed for that, and neither belongs on meal_slot.
--
-- meal_slot stays the coarse clinical vocabulary that dietary analysis groups by
-- (6 values). The app asks a finer question -- it distinguishes a morning snack
-- from an evening one because postprandial context differs -- so meal_type carries
-- the 9-value application vocabulary alongside it. Overloading meal_slot would
-- either lose that distinction or corrupt the clinical grouping.
-- ============================================================================

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'app' AND table_name = 'meal_log'
          AND column_name = 'meal_type'
    ) THEN
        ALTER TABLE app.meal_log ADD COLUMN meal_type text;

        ALTER TABLE app.meal_log ADD CONSTRAINT meal_log_meal_type_ck
            CHECK (meal_type IS NULL OR meal_type IN (
                'breakfast', 'morning_snack', 'tiffin', 'lunch', 'afternoon_snack',
                'evening_snack', 'dinner', 'late_night_snack', 'other'
            ));
    END IF;
END $$;

COMMENT ON COLUMN app.meal_log.meal_type IS
'Application-level meal classification (9 values). meal_slot remains the coarse '
'clinical grouping (6 values); this does not replace it.';

COMMENT ON COLUMN app.meal_log.notes IS
'The user''s own words for this meal — the text they typed, or the caption on a '
'photo log. Kept verbatim so a resolution can be re-run against the original input.';

-- The development user.
--
-- There is no authentication yet (see README "Known gaps"; it is Phase 6 work).
-- Rather than let the API invent a user row per request, or scatter NULL user_ids
-- through a NOT NULL column, every unattributed log lands on this one fixed
-- pseudonymous row. It is a placeholder for an identity system, not an identity
-- system: anyone who can reach the API is this user.
INSERT INTO app.app_user (user_id, preferred_lang, is_anonymised)
VALUES ('00000000-0000-0000-0000-000000000001', 'en', true)
ON CONFLICT (user_id) DO NOTHING;

COMMENT ON TABLE app.meal_log IS
'One logged meal. Rows are soft-deleted via deleted_at so a mis-log can be undone '
'and so deletion does not silently rewrite a person''s dietary history.';
