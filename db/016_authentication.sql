-- ============================================================================
-- Pathyam · 016 · Authentication
-- ============================================================================
-- Until now there was no authentication at all. Identity came from an unverified
-- X-Pathyam-User header, which made the per-user code paths real and testable but
-- meant anyone who could reach the API could claim to be anyone. It also meant the
-- cgm_telemetry consent purpose (db/014) could not be enforced: there was no
-- authenticated subject whose consent there was to check.
--
-- TWO DESIGN CHOICES WORTH THE COMMENT
--
-- 1. Credentials live in their OWN table, not on app.app_user.
--    db/006 says so explicitly: app_user is pseudonymous by design so that ML and
--    analytics roles can be granted it without ever touching contact data. Putting
--    an email column on it would quietly undo that, so email and password hash go
--    here and this table is the one to restrict.
--
-- 2. Sessions are opaque tokens in a table, not JWTs.
--    A JWT cannot be revoked before it expires without a denylist, which is a
--    session table with extra steps and a signing key to manage. For a health
--    application, "log out everywhere, now" has to actually work. Only the token's
--    SHA-256 is stored, so a dump of this table does not hand over live sessions.
-- ============================================================================

CREATE TABLE IF NOT EXISTS app.user_credential (
    credential_id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id         uuid NOT NULL UNIQUE
                        REFERENCES app.app_user(user_id) ON DELETE CASCADE,

    -- Case-folded at the application layer before it reaches here, so
    -- Alice@example.com and alice@example.com cannot become two accounts.
    email           text NOT NULL UNIQUE CHECK (email = lower(email)),

    -- scrypt. The salt and cost parameters travel with the hash in a single
    -- self-describing string, so parameters can be raised later without a
    -- migration: an old hash still says how to verify itself.
    password_hash   text NOT NULL,

    created_at      timestamptz NOT NULL DEFAULT now(),
    last_login_at   timestamptz,
    -- Simple online-guessing brake. Reset on success.
    failed_attempts smallint NOT NULL DEFAULT 0,
    locked_until    timestamptz
);

COMMENT ON TABLE app.user_credential IS
'Direct identifiers and password material. RESTRICT ACCESS TO THIS TABLE: it is '
'deliberately separate from app.app_user so analytics and ML roles can read user '
'records without ever seeing contact data.';

CREATE TABLE IF NOT EXISTS app.user_session (
    session_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id       uuid NOT NULL REFERENCES app.app_user(user_id) ON DELETE CASCADE,

    -- SHA-256 of the bearer token. The token itself is shown once, at login, and
    -- never stored: a leak of this table must not yield usable sessions. SHA-256 is
    -- right here and scrypt is not -- the token is 256 bits of CSPRNG output, so
    -- there is no low-entropy secret to slow an attacker down over.
    token_sha256  bytea NOT NULL UNIQUE,

    created_at    timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz NOT NULL,
    revoked_at    timestamptz,
    last_seen_at  timestamptz,
    user_agent    text,

    CONSTRAINT user_session_expiry_ck CHECK (expires_at > created_at)
);

CREATE INDEX IF NOT EXISTS user_session_user_idx
    ON app.user_session (user_id) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS user_session_expiry_idx
    ON app.user_session (expires_at) WHERE revoked_at IS NULL;

COMMENT ON COLUMN app.user_session.token_sha256 IS
'SHA-256 of the bearer token. The plaintext token is returned once at login and is '
'never persisted.';

-- Authentication is itself a processing purpose, and the consent record has to be
-- able to say the account existed.
INSERT INTO app.consent_purpose (purpose_key, description_en, is_required_for_service)
VALUES ('account',
        'Keep your account so you can sign in and see your own data.',
        true)
ON CONFLICT (purpose_key) DO NOTHING;
