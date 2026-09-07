-- ============================================================================
-- Pathyam · 017 · Consent enforcement
-- ============================================================================
-- Consent purposes were registered (db/006, db/014, db/016) and granted at sign-up,
-- but nothing checked them at write time. Now /v1/log requires `core_service` and
-- /v1/cgt/telemetry requires `cgm_telemetry`, and a write without the relevant
-- consent is refused with 403.
--
-- THE AWKWARD CASE, handled here rather than papered over:
--
-- app.app_user rows are created implicitly on first write for the development
-- identity path. A user created that way has never been shown a notice and cannot
-- meaningfully have consented, so it gets no consent rows and its writes are
-- refused -- which is the correct outcome, not a bug to work around.
--
-- The single fixed development user seeded in db/013 is the exception, because it
-- is a fiction that exists so local development and the pre-auth test suite work.
-- Its consent is granted HERE, explicitly and visibly, rather than by a special
-- case inside the enforcement code. A backdoor in the check is how these gaps
-- reappear; a seeded row is auditable and obvious.
--
-- It deliberately does NOT get cgm_telemetry: glucose telemetry is optional and
-- sensitive, so even the development user has to grant it like anyone else, and
-- the enforcement path stays genuinely exercised.
-- ============================================================================

INSERT INTO app.user_consent
    (user_id, purpose_id, notice_version, notice_lang, evidence)
SELECT '00000000-0000-0000-0000-000000000001',
       p.purpose_id,
       'dev-seed',
       'en',
       jsonb_build_object(
           'note', 'Seeded for the fixed development user by db/017. Not a record '
                   'of a real person consenting to anything.')
  FROM app.consent_purpose p
 WHERE p.purpose_key IN ('account', 'core_service')
   AND NOT EXISTS (
        SELECT 1 FROM app.user_consent c
         WHERE c.user_id = '00000000-0000-0000-0000-000000000001'
           AND c.purpose_id = p.purpose_id
   );

COMMENT ON TABLE app.user_consent IS
'Consent records. Checked at write time: app.meal_log writes require core_service '
'and app.cgm_reading writes require cgm_telemetry. A user row created implicitly on '
'first write has no consent and its writes are refused.';
