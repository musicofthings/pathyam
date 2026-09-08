-- ============================================================================
-- Pathyam · 019 · Consent for third-party vision processing
-- ============================================================================
-- /v1/vision/resolve and /v1/perception/analyze send a photograph the user took of
-- their own meal to an external model provider. Until now neither endpoint even
-- required authentication, so there was no subject whose consent could be checked
-- and nothing recorded that the photo had left the system at all.
--
-- WHY THIS IS NOT COVERED BY `model_training`
-- -------------------------------------------
-- db/006 already registers `model_training`: "Use your food photos and corrections
-- to improve Pathyam's recognition models." That is Pathyam training its own model
-- on data it holds. This is a different act — the photograph leaves Pathyam and is
-- processed by a third party under that provider's policy, not ours — and consenting
-- to one is not consenting to the other.
--
-- WHY IT IS OPTIONAL, NOT REQUIRED FOR SERVICE
-- --------------------------------------------
-- Meals can be logged as text ("2 idli and sambar"), which is the primary path and
-- needs no third party. Photography is a convenience on top. Consent that must be
-- given to use the product at all is not freely given, so this is registered
-- optional and the app degrades to text logging when it is refused.
--
-- WHAT THE USER IS AGREEING TO, AND THE PART THAT IS NOT THEIRS TO DECIDE
-- -----------------------------------------------------------------------
-- The user agrees that their photograph may be sent to an external provider. Which
-- provider, and whether that provider may train on what it receives, is an operator
-- configuration (PATHYAM_VISION_MODEL) the user cannot see or choose. Free
-- OpenRouter endpoints may train on or publish their inputs; paid ones generally do
-- not. So the API reports, per response, whether the model that read the photograph
-- was a free training-permitted endpoint — the user cannot consent to a fact that is
-- withheld from them.
--
-- Not seeded for the fixed development user, for the same reason db/017 withholds
-- cgm_telemetry from it: an optional, sensitive purpose should be granted like
-- anyone else's, so the enforcement path stays genuinely exercised.
-- ============================================================================

INSERT INTO app.consent_purpose (purpose_key, description_en, is_required_for_service)
VALUES ('vision_third_party',
        'Send photographs of your meals to an external AI provider so the app can '
        'identify what is on the plate. The photo leaves Pathyam and is handled '
        'under that provider''s policy. You can log meals as text instead.',
        false)
ON CONFLICT (purpose_key) DO NOTHING;

COMMENT ON TABLE app.user_consent IS
'Consent records. Checked at write time: app.meal_log writes require core_service, '
'app.cgm_reading writes require cgm_telemetry, and sending a photograph to an '
'external model provider requires vision_third_party. A user row created implicitly '
'on first write has no consent and its writes are refused.';
