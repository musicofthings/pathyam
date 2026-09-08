-- ============================================================================
-- Pathyam · 020 · Password reset
-- ============================================================================
-- An account whose password was forgotten was unrecoverable: there was no reset
-- path at all, and no way to reach the user to offer one.
--
-- SAME TOKEN DISCIPLINE AS app.user_session
-- -----------------------------------------
-- Only the SHA-256 of the token is stored. The token itself goes out in the email
-- and is never written down, so a leak of this table yields nothing usable. SHA-256
-- is right and scrypt is not, for the same reason as sessions: the token is 256 bits
-- of CSPRNG output, so there is no low-entropy secret for an attacker to grind.
--
-- SINGLE USE, SHORT LIFE
-- ----------------------
-- `used_at` is set the moment a token is redeemed and a used token is never accepted
-- again -- a reset link sits in an inbox, which is exactly the kind of place a
-- long-lived reusable credential should not be. Requesting a new reset invalidates
-- any outstanding one for that user, so a link the user did not ask for cannot be
-- kept alive by an attacker who requested it first.
--
-- WHAT IT DELIBERATELY DOES NOT RECORD
-- ------------------------------------
-- No email address column. The address is in app.user_credential, which is
-- access-restricted precisely so contact data lives in one place; copying it here to
-- save a join would spread direct identifiers across the schema for no benefit.
-- ============================================================================

CREATE TABLE IF NOT EXISTS app.password_reset (
    reset_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id       uuid NOT NULL REFERENCES app.app_user(user_id) ON DELETE CASCADE,

    token_sha256  bytea NOT NULL UNIQUE,

    created_at    timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz NOT NULL,
    used_at       timestamptz,
    -- Requesting address, for abuse investigation. Same caveat as the rate limiter:
    -- meaningful only as far as the proxy configuration is trustworthy.
    requested_ip  text,

    CONSTRAINT password_reset_expiry_ck CHECK (expires_at > created_at)
);

-- The lookup is "live tokens for this user", used to invalidate outstanding ones
-- when a new reset is requested.
CREATE INDEX IF NOT EXISTS password_reset_user_idx
    ON app.password_reset (user_id) WHERE used_at IS NULL;

COMMENT ON TABLE app.password_reset IS
'Single-use password reset tokens, stored only as SHA-256. Redeeming one revokes '
'every live session for that user: a reset exists to evict whoever should not be there.';

COMMENT ON COLUMN app.password_reset.token_sha256 IS
'SHA-256 of the token. The token is emailed once and never stored, so a leak of this '
'table yields nothing usable.';
