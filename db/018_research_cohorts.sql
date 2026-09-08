-- ============================================================================
-- Pathyam · 018 · Research cohort datasets
-- ============================================================================
-- Third-party research datasets — CGM traces paired with meal records — used to
-- exercise the postprandial pipeline before Pathyam has participants of its own.
--
-- WHY A SEPARATE SCHEMA, NOT app.cgm_reading
-- ------------------------------------------
-- app.* holds data about Pathyam's own users, written under a consent purpose they
-- granted (app.consent_purpose 'cgm_telemetry', enforced since db/017). Research
-- participants granted consent to a different study, under a different protocol,
-- years ago. Loading them into app.app_user would manufacture users who never
-- agreed to anything here, and would either bypass the consent check or require
-- forging consent rows. Both are worse than a second schema.
--
-- The licence is the other reason. A NonCommercial dataset must stay separable
-- from production data, or it cannot be excluded at release time.
--
-- WHAT THIS SCHEMA IS NOT FOR
-- ---------------------------
-- Fitting the coefficients that ship. Every dataset here records the cohort it was
-- drawn from (`population_note`) precisely because glycaemic response does not
-- transfer across populations or cuisines. See the CGMacros note in
-- pathyam_engine/authoring/cgmacros.py.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS research;

COMMENT ON SCHEMA research IS
'Third-party research cohorts under their own licences. Never mixed with app.* user '
'data. Every table here traces to a ref.source row whose is_commercial_cleared flag '
'is the release gate.';


-- --------------------------------------------------------------- dataset ----
CREATE TABLE IF NOT EXISTS research.dataset (
    dataset_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dataset_key     text NOT NULL UNIQUE,              -- 'CGMACROS-1.0.0'
    -- Every dataset rests on exactly one source row, which carries the licence and
    -- the is_commercial_cleared flag. Same discipline as ref.composition_value.
    source_id       bigint NOT NULL REFERENCES ref.source(source_id),

    -- Who the participants actually were. Required, and deliberately free text:
    -- a glycaemic model fitted on one population does not transfer to another, and
    -- the single most common way to get that wrong is to not write it down.
    population_note text NOT NULL,

    -- FALSE when meal masses were estimated (from photographs, self-report, or a
    -- percentage-consumed field) rather than weighed. A dataset with FALSE here
    -- cannot substitute for the golden meal set, whose whole purpose is weighed
    -- component masses.
    portions_are_weighed boolean NOT NULL,

    -- Many public CGM datasets shift timestamps to de-identify. Absolute dates are
    -- then meaningless; only intervals within a subject survive. Recording this
    -- stops anyone reading seasonality or time-of-day effects out of noise.
    timestamps_are_shifted boolean NOT NULL,

    subject_count   integer,
    retrieved_on    date,
    notes           text,
    created_at      timestamptz NOT NULL DEFAULT now()
);

COMMENT ON COLUMN research.dataset.population_note IS
'Who the cohort was: country, recruitment site, ethnicity, cuisine of the meals. '
'Required because glycaemic response does not transfer across populations.';


-- --------------------------------------------------------------- subject ----
CREATE TABLE IF NOT EXISTS research.subject (
    subject_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dataset_id      bigint NOT NULL REFERENCES research.dataset(dataset_id) ON DELETE CASCADE,
    -- The id used by the dataset itself, so a row can be traced back to the file.
    external_ref    text   NOT NULL,

    age_years       smallint CHECK (age_years BETWEEN 0 AND 120),
    sex             text     CHECK (sex IN ('F', 'M', 'other', 'unknown')),
    -- Verbatim from the source, never normalised into our own vocabulary: the
    -- categories a study used are part of what its results mean.
    self_identified_ethnicity text,
    bmi             numeric(4,1) CHECK (bmi BETWEEN 10 AND 80),
    glycaemic_status text CHECK (glycaemic_status IN
                          ('normoglycaemic', 'prediabetes', 'type_2_diabetes',
                           'type_1_diabetes', 'unknown')),
    hba1c_percent   numeric(3,1),
    fasting_glucose_mg_dl numeric(5,1),

    CONSTRAINT subject_unique_ck UNIQUE (dataset_id, external_ref)
);


-- ----------------------------------------------------------- cgm reading ----
CREATE TABLE IF NOT EXISTS research.cgm_reading (
    reading_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    subject_id      bigint NOT NULL REFERENCES research.subject(subject_id) ON DELETE CASCADE,
    reading_at      timestamptz NOT NULL,
    glucose_mg_dl   numeric(5,1) NOT NULL CHECK (glucose_mg_dl BETWEEN 40 AND 450),

    -- Which sensor. Not decoration: CGMacros wore a Libre Pro and a Dexcom G6 Pro
    -- simultaneously, and the two disagree by a clinically meaningful margin. A
    -- model fitted across both without distinguishing them is fitting sensor bias.
    sensor          text NOT NULL,

    CONSTRAINT research_cgm_unique_ck UNIQUE (subject_id, reading_at, sensor)
);

CREATE INDEX IF NOT EXISTS research_cgm_subject_time_idx
    ON research.cgm_reading (subject_id, reading_at);

COMMENT ON COLUMN research.cgm_reading.sensor IS
'Sensor that produced the reading. Two sensors on one subject at one instant are '
'two rows, not one: they disagree, and averaging them hides the disagreement.';


-- ------------------------------------------------------------------ meal ----
CREATE TABLE IF NOT EXISTS research.meal (
    meal_id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    subject_id      bigint NOT NULL REFERENCES research.subject(subject_id) ON DELETE CASCADE,
    consumed_at     timestamptz NOT NULL,
    meal_type       text,

    energy_kcal     numeric(7,2),
    carbohydrate_g  numeric(7,2),
    protein_g       numeric(7,2),
    fat_g           numeric(7,2),
    fibre_g         numeric(7,2),

    -- Share of the served meal the participant actually ate, where the dataset
    -- reports it. NULL means the dataset did not measure it -- which is not the
    -- same as 100, and must not be silently treated as 100.
    fraction_consumed numeric(4,3) CHECK (fraction_consumed BETWEEN 0 AND 1),

    description     text,
    photo_ref       text,

    CONSTRAINT research_meal_unique_ck UNIQUE (subject_id, consumed_at)
);

CREATE INDEX IF NOT EXISTS research_meal_subject_time_idx
    ON research.meal (subject_id, consumed_at);


-- ------------------------------------------------- postprandial pairing ----
-- Deliberately the same shape and the same rules as app.v_postprandial_reading,
-- so a fitting routine reads one or the other without branching: three-hour
-- window, reading attributed to the most recent meal at or before it.
CREATE OR REPLACE VIEW research.v_postprandial_reading AS
SELECT m.meal_id,
       m.subject_id,
       d.dataset_key,
       m.consumed_at,
       m.meal_type,
       r.sensor,
       r.reading_at,
       r.glucose_mg_dl,
       EXTRACT(EPOCH FROM (r.reading_at - m.consumed_at)) / 60.0 AS minutes_since_meal
  FROM research.meal m
  JOIN research.subject s  ON s.subject_id = m.subject_id
  JOIN research.dataset d  ON d.dataset_id = s.dataset_id
  JOIN research.cgm_reading r
    ON r.subject_id = m.subject_id
   AND r.reading_at >= m.consumed_at
   AND r.reading_at <  m.consumed_at + interval '3 hours'
 WHERE m.consumed_at = (
        SELECT max(m2.consumed_at)
          FROM research.meal m2
         WHERE m2.subject_id = m.subject_id
           AND m2.consumed_at <= r.reading_at
   );


-- ------------------------------------------------- COMPLIANCE CANARY #3 ----
-- ref.v_uncleared_values covers ref.composition_value and nothing else. A research
-- dataset registered with is_commercial_cleared = false was therefore invisible to
-- the release gate: the flag was set correctly and checked nowhere. This view is
-- the missing half.
CREATE OR REPLACE VIEW research.v_uncleared_datasets AS
SELECT s.source_key,
       s.licence,
       s.permission_ref,
       d.dataset_key,
       d.population_note,
       count(DISTINCT sub.subject_id) AS subject_count,
       count(r.reading_id)            AS reading_count
  FROM research.dataset d
  JOIN ref.source s          ON s.source_id  = d.source_id
  LEFT JOIN research.subject sub ON sub.dataset_id = d.dataset_id
  LEFT JOIN research.cgm_reading r ON r.subject_id = sub.subject_id
 WHERE NOT s.is_commercial_cleared
 GROUP BY s.source_key, s.licence, s.permission_ref, d.dataset_key, d.population_note
 ORDER BY reading_count DESC;

COMMENT ON VIEW research.v_uncleared_datasets IS
'Release gate for research cohorts, the counterpart to ref.v_uncleared_values. '
'Non-empty means the build carries research data whose commercial reuse rights are '
'unestablished.';


-- --------------------------------------------------- THE SINGLE RELEASE GATE ----
-- Two canaries is one too many to remember, and the way this schema got shipped
-- without gate coverage in the first place was someone checking the one view they
-- knew about. Check this instead: it is empty or the build is not releasable.
CREATE OR REPLACE VIEW ref.v_release_blockers AS
SELECT 'composition'::text AS asset,
       source_key,
       licence,
       permission_ref,
       value_count AS row_count,
       NULL::text  AS detail
  FROM ref.v_uncleared_values
UNION ALL
SELECT 'research_cohort'::text,
       source_key,
       licence,
       permission_ref,
       reading_count,
       dataset_key || ' — ' || population_note
  FROM research.v_uncleared_datasets;

COMMENT ON VIEW ref.v_release_blockers IS
'THE release gate. Unions every asset class whose commercial reuse rights are '
'unestablished — composition values and research cohorts. Must be empty before any '
'commercial release. Check this rather than the per-class views: a new asset class '
'that forgets to join here is the failure mode this view exists to prevent.';
