-- ============================================================================
-- Pathyam · 006 · Users and consent  (DPDP Act 2023 + DPDP Rules 2025)
-- ============================================================================
-- The launch plan is: ship on public data, then fine-tune on user-contributed
-- images and corrections. That plan has a legal precondition.
--
-- DPDP requires consent that is "free, specific, informed, unconditional and
-- unambiguous". A bundled "we may use your data to improve our services" clause in
-- the ToS is almost certainly NOT specific enough to cover model training. The
-- failure mode is accumulating 100,000 labelled images that cannot lawfully be used.
--
-- The design response, implemented below:
--   1. model_training is a SEPARATE consent purpose with its own record.
--   2. Training eligibility is DERIVED AT QUERY TIME (ml.v_training_eligible_user),
--      never materialised. Withdrawal takes effect on the next query, not on the
--      next batch job.
--   3. Consent records are retained 7 years per the Rules, so a user row is never
--      hard-deleted — it is anonymised (app.anonymise_user).
-- ============================================================================

CREATE TABLE app.app_user (
    user_id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at      timestamptz NOT NULL DEFAULT now(),
    region_id       bigint REFERENCES ref.region(region_id),
    preferred_lang  text NOT NULL DEFAULT 'en' CHECK (preferred_lang IN ('en','ta','te','ml','kn')),
    year_of_birth   smallint CHECK (year_of_birth BETWEEN 1900 AND 2100),
    sex_at_birth    text CHECK (sex_at_birth IN ('female','male','intersex','undisclosed')),
    -- Deliberately NO email / phone / name here. Direct identifiers live in a
    -- separate, access-restricted, encrypted table so that analytics and ML
    -- workloads can be granted app.app_user without ever touching contact data.
    is_anonymised   boolean NOT NULL DEFAULT false,
    deleted_at      timestamptz
);

COMMENT ON TABLE app.app_user IS
'Pseudonymous user record. Direct identifiers (email, phone, name) belong in a '
'separate restricted table, not here — so ML and analytics roles can read this safely.';

-- ------------------------------------------------------- consent purposes ----
CREATE TABLE app.consent_purpose (
    purpose_id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    purpose_key             text NOT NULL UNIQUE,
    description_en          text NOT NULL,
    description_i18n        jsonb NOT NULL DEFAULT '{}',
    is_required_for_service boolean NOT NULL DEFAULT false,
    created_at              timestamptz NOT NULL DEFAULT now()
);

INSERT INTO app.consent_purpose (purpose_key, description_en, is_required_for_service) VALUES
  ('core_service',
   'Store your meal logs so the app can show your intake and history.', true),
  ('model_training',
   'Use your food photos and corrections to improve Pathyam''s recognition models.', false),
  ('research_publication',
   'Include your de-identified data in aggregate research outputs and publications.', false),
  ('clinician_sharing',
   'Share your logs with a clinician or dietitian you nominate.', false),
  ('marketing',
   'Send you product updates and offers.', false);

-- ---------------------------------------------------------- user consent ----
CREATE TABLE app.user_consent (
    consent_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id         uuid   NOT NULL REFERENCES app.app_user(user_id) ON DELETE RESTRICT,
    purpose_id      bigint NOT NULL REFERENCES app.consent_purpose(purpose_id),
    granted_at      timestamptz NOT NULL DEFAULT now(),
    withdrawn_at    timestamptz,
    -- Which notice text, in which language, the user actually saw. Without this you
    -- cannot demonstrate that consent was "informed".
    notice_version  text NOT NULL,
    notice_lang     text NOT NULL CHECK (notice_lang IN ('en','ta','te','ml','kn')),
    evidence        jsonb NOT NULL,   -- UI snapshot, timestamp, request metadata
    -- DPDP Rules 2025: consent, notice and sharing records retained >= 7 years.
    -- NOT a generated column: timestamptz + interval is STABLE, not IMMUTABLE
    -- (the result depends on the session TimeZone), and PostgreSQL rejects
    -- non-immutable generation expressions. A BEFORE trigger is the correct tool.
    retain_until    date NOT NULL,

    CONSTRAINT user_consent_window_ck CHECK (withdrawn_at IS NULL OR withdrawn_at >= granted_at),
    CONSTRAINT user_consent_retention_ck CHECK (retain_until >= (granted_at AT TIME ZONE 'UTC')::date)
);

CREATE OR REPLACE FUNCTION app.set_consent_retention()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    -- Pinned to UTC so the retention date does not drift with session timezone.
    NEW.retain_until := ((NEW.granted_at AT TIME ZONE 'UTC') + interval '7 years')::date;
    RETURN NEW;
END;
$$;

CREATE TRIGGER user_consent_retention_trg
    BEFORE INSERT OR UPDATE OF granted_at ON app.user_consent
    FOR EACH ROW EXECUTE FUNCTION app.set_consent_retention();

COMMENT ON COLUMN app.user_consent.user_id IS
'ON DELETE RESTRICT is intentional. Consent records must outlive the account (7 years). '
'Erasure is handled by app.anonymise_user(), not by DELETE.';

CREATE INDEX user_consent_lookup_idx ON app.user_consent (user_id, purpose_id)
                                     WHERE withdrawn_at IS NULL;
-- One live grant per user per purpose. Re-granting after withdrawal creates a new row.
CREATE UNIQUE INDEX user_consent_active_idx ON app.user_consent (user_id, purpose_id)
                                            WHERE withdrawn_at IS NULL;

-- ------------------------------------------------ erasure by anonymisation ----
CREATE OR REPLACE FUNCTION app.anonymise_user(p_user_id uuid)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    -- Withdraw every live consent first, so the training corpus view drops this
    -- user's rows immediately rather than at the next pipeline run.
    UPDATE app.user_consent
       SET withdrawn_at = now()
     WHERE user_id = p_user_id AND withdrawn_at IS NULL;

    UPDATE app.app_user
       SET year_of_birth = NULL,
           sex_at_birth  = NULL,
           region_id     = NULL,
           is_anonymised = true,
           deleted_at    = now()
     WHERE user_id = p_user_id;

    -- Detach stored media. The object-store deletion is the caller's responsibility
    -- and must be recorded separately; this only removes the pointer.
    UPDATE app.meal_log
       SET image_ref = NULL, notes = NULL, deleted_at = coalesce(deleted_at, now())
     WHERE user_id = p_user_id;
END;
$$;

COMMENT ON FUNCTION app.anonymise_user(uuid) IS
'DPDP erasure path. Withdraws consents (which instantly removes the user from the '
'training corpus view), strips attributes, and detaches media pointers — while '
'preserving the consent audit trail the Rules require for 7 years.';
