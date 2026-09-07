-- ============================================================================
-- Pathyam · 014 · Continuous glucose readings
-- ============================================================================
-- /v1/cgt/telemetry previously echoed its input and stored nothing, so there was
-- no glucose data anywhere in the system. That is also why the CGT curve remains
-- illustrative: its coefficients cannot be fitted against readings that were never
-- kept. This table is the prerequisite for making that model real.
--
-- SENSITIVITY
-- Interstitial glucose is health data about an identifiable person's metabolic
-- condition, and a continuous trace is more revealing than any single reading --
-- it shows when someone eats, sleeps, exercises and is ill. It is stored against
-- the pseudonymous app.app_user id and nothing else, consistent with the rest of
-- the app schema, and a consent purpose is registered for it below.
--
-- NOTE: consent is recorded but NOT YET ENFORCED at the API. There is no
-- authentication (see README "Known gaps"), so there is no authenticated subject
-- whose consent could be checked. Wiring the check belongs with auth, in Phase 6.
-- ============================================================================

CREATE TABLE IF NOT EXISTS app.cgm_reading (
    reading_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id        uuid        NOT NULL REFERENCES app.app_user(user_id) ON DELETE CASCADE,

    -- When the sensor measured it, not when we received it. Backfill after a
    -- connectivity gap is normal and must not distort the trace.
    reading_at     timestamptz NOT NULL,
    ingested_at    timestamptz NOT NULL DEFAULT now(),

    -- Range matches the API schema and the reporting range of common CGM sensors.
    -- Values outside it are sensor errors, not measurements.
    glucose_mg_dl  numeric(5,1) NOT NULL CHECK (glucose_mg_dl BETWEEN 40 AND 450),

    trend_arrow    text CHECK (trend_arrow IN
                       ('rapidly_rising','rising','flat','falling','rapidly_falling')),
    device         text,

    -- Sensors resend on reconnect and clients retry. Idempotent on the natural key
    -- so a replayed batch updates rather than duplicating: a doubled reading would
    -- silently bias any curve fitted from this table.
    CONSTRAINT cgm_reading_unique_ck UNIQUE (user_id, reading_at)
);

-- The access pattern is "this user's readings over a window", for charting and for
-- pairing against meals. DESC matches the common "most recent first" read.
CREATE INDEX IF NOT EXISTS cgm_reading_user_time_idx
    ON app.cgm_reading (user_id, reading_at DESC);

COMMENT ON TABLE app.cgm_reading IS
'Interstitial glucose readings. Health data: store against the pseudonymous user id '
'only, never alongside direct identifiers. reading_at is sensor time, not receipt time.';

-- ------------------------------------------------- postprandial pairing ----
-- The join that makes fitting possible: each logged meal with the readings that
-- follow it inside a three-hour window, expressed as minutes since the meal.
--
-- Three hours because that is the window the illustrative curve spans. A reading
-- is attributed to the most recent meal at or before it, so back-to-back meals do
-- not both claim the same trace -- overlapping excursions are a real confounder and
-- this view does not pretend to resolve them, it just avoids double-counting.
CREATE OR REPLACE VIEW app.v_postprandial_reading AS
SELECT m.meal_log_id,
       m.user_id,
       m.consumed_at,
       m.meal_type,
       r.reading_at,
       r.glucose_mg_dl,
       EXTRACT(EPOCH FROM (r.reading_at - m.consumed_at)) / 60.0 AS minutes_since_meal
  FROM app.meal_log m
  JOIN app.cgm_reading r
    ON r.user_id = m.user_id
   AND r.reading_at >= m.consumed_at
   AND r.reading_at <  m.consumed_at + interval '3 hours'
 WHERE m.deleted_at IS NULL
   AND m.consumed_at = (
        SELECT max(m2.consumed_at)
          FROM app.meal_log m2
         WHERE m2.user_id = m.user_id
           AND m2.deleted_at IS NULL
           AND m2.consumed_at <= r.reading_at
   );

COMMENT ON VIEW app.v_postprandial_reading IS
'Each meal paired with the glucose readings in the 3h after it, as minutes since the '
'meal. This is the input a fitted CGT model would be trained and scored against; the '
'shipped model is illustrative and has never been compared to it.';

-- ------------------------------------------------------ consent purpose ----
INSERT INTO app.consent_purpose (purpose_key, description_en, is_required_for_service)
VALUES ('cgm_telemetry',
        'Store readings from your continuous glucose monitor so the app can show '
        'your glucose alongside your meals.',
        false)
ON CONFLICT (purpose_key) DO NOTHING;
