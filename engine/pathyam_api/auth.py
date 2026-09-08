"""Authentication: passwords, sessions, and who a request is acting for.

Replaces the unverified ``X-Pathyam-User`` header, which let anyone who could reach
the API claim to be anyone, and which made the ``cgm_telemetry`` consent purpose
unenforceable because there was no authenticated subject to check.

PASSWORD HASHING
----------------
``hashlib.scrypt`` from the standard library. It is memory-hard, which is the
property that matters against GPU cracking, and it avoids adding a dependency for
something this security-sensitive. Parameters below follow the interactive-login
profile from the scrypt paper (N=2^15, r=8, p=1, ~32 MB per hash).

The hash is stored self-describing -- ``scrypt$N$r$p$salt$hash`` -- so the cost can
be raised later without a migration: an old hash still says how to verify itself,
and is transparently re-hashed at the user's next successful login.

SESSIONS
--------
Opaque 256-bit tokens, stored only as SHA-256. The plaintext is returned once at
login. SHA-256 is the right choice for the token and scrypt is not: the token is
CSPRNG output with no low-entropy secret to slow an attacker down over, and hashing
it on every request would add ~30 ms to every authenticated call for nothing.

NOT IMPLEMENTED, deliberately
-----------------------------
Password reset and email verification both need somewhere to send mail, which this
deployment does not have. Rate limiting is per-account only (a lockout counter);
there is no per-IP limit, which belongs at the edge rather than here. Both are in
the README rather than left for someone to discover.
"""

from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import hmac
import os
import re
import secrets
import uuid
from dataclasses import dataclass
from typing import Any

__all__ = [
    "AuthError",
    "InvalidCredentials",
    "AccountLocked",
    "SessionExpired",
    "AuthRepository",
    "hash_password",
    "verify_password",
    "SESSION_TTL_HOURS",
    "RESET_TTL_HOURS",
    "MIN_PASSWORD_LENGTH",
]

# scrypt interactive-login parameters. ~32 MB and ~100 ms per hash on commodity
# hardware: slow enough to matter to an attacker, fast enough for a login.
_SCRYPT_N = 1 << 15
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SALT_BYTES = 16

SESSION_TTL_HOURS = 24 * 14
# Reset links live in an inbox, which is a place other people sometimes reach. An
# hour is long enough to walk to a laptop and short enough that a stale link in a
# mailbox is not a standing key to the account.
RESET_TTL_HOURS = 1
MIN_PASSWORD_LENGTH = 10
_TOKEN_BYTES = 32
_MAX_FAILED_ATTEMPTS = 8
_LOCKOUT_MINUTES = 15

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validate_password(password: str) -> None:
    """The minimum, enforced here as well as at the schema.

    schemas.py sets min_length=10 on the HTTP boundary, which covers the API. This
    guard covers everything else -- reset, and any future caller reaching the
    repository directly -- so the rule cannot be bypassed by not going through
    FastAPI. Registration and reset must agree, or a reset becomes the way to set a
    password registration would have refused.
    """
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
_MIN_PASSWORD_CHARS = 10


class AuthError(RuntimeError):
    """Base class for authentication failures."""


class InvalidCredentials(AuthError):
    """Wrong email or password. Deliberately does not say which."""


class AccountLocked(AuthError):
    """Too many failed attempts."""


class SessionExpired(AuthError):
    """No session, or one that is expired or revoked."""


# ------------------------------------------------------------- passwords --

def hash_password(password: str, *, n: int = _SCRYPT_N) -> str:
    """Hash a password with scrypt. Returns a self-describing string."""
    if len(password) < _MIN_PASSWORD_CHARS:
        raise ValueError(
            f"password must be at least {_MIN_PASSWORD_CHARS} characters"
        )
    salt = secrets.token_bytes(_SALT_BYTES)
    derived = hashlib.scrypt(
        password.encode("utf-8"), salt=salt,
        n=n, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_SCRYPT_DKLEN,
        maxmem=n * _SCRYPT_R * 200,
    )
    b64 = lambda raw: base64.b64encode(raw).decode("ascii")  # noqa: E731
    return f"scrypt${n}${_SCRYPT_R}${_SCRYPT_P}${b64(salt)}${b64(derived)}"


def verify_password(password: str, stored: str) -> bool:
    """Check a password against a stored hash. Constant-time on the digest."""
    try:
        scheme, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        n, r, p = int(n), int(r), int(p)
    except (ValueError, TypeError):
        return False

    candidate = hashlib.scrypt(
        password.encode("utf-8"), salt=salt,
        n=n, r=r, p=p, dklen=len(expected), maxmem=n * r * 200,
    )
    return hmac.compare_digest(candidate, expected)


def _token_digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def normalise_email(email: str) -> str:
    return (email or "").strip().lower()


@dataclass(frozen=True)
class Session:
    user_id: str
    token: str | None      # present only at creation
    expires_at: _dt.datetime


class AuthRepository:
    """Accounts and sessions. One instance per request connection."""

    def __init__(self, conn) -> None:
        self._conn = conn

    # ------------------------------------------------------ registration --

    def register(self, email: str, password: str) -> str:
        """Create an account. Returns the new pseudonymous user_id."""
        email = normalise_email(email)
        if not _EMAIL_RE.match(email):
            raise ValueError("that does not look like an email address")
        _validate_password(password)

        password_hash = hash_password(password)
        user_id = str(uuid.uuid4())

        with self._conn.cursor() as cur:
            cur.execute("SELECT 1 FROM app.user_credential WHERE email = %s", (email,))
            if cur.fetchone():
                # Registration necessarily reveals whether an address is taken --
                # the alternative is silently not creating the account. Login does
                # NOT leak it, which is where it matters.
                raise ValueError("an account already exists for that address")

            cur.execute(
                """INSERT INTO app.app_user (user_id, preferred_lang, is_anonymised)
                   VALUES (%s, 'en', false)""",
                (user_id,),
            )
            cur.execute(
                """INSERT INTO app.user_credential (user_id, email, password_hash)
                   VALUES (%s, %s, %s)""",
                (user_id, email, password_hash),
            )
        self._conn.commit()
        return user_id

    # ------------------------------------------------------------- login --

    def login(self, email: str, password: str, *, user_agent: str | None = None) -> Session:
        email = normalise_email(email)
        now = _dt.datetime.now(_dt.timezone.utc)

        with self._conn.cursor() as cur:
            cur.execute(
                """SELECT user_id, password_hash, failed_attempts, locked_until
                     FROM app.user_credential WHERE email = %s""",
                (email,),
            )
            row = cur.fetchone()

            if row is None:
                # Spend comparable time on an unknown address so response timing
                # does not reveal which addresses are registered.
                verify_password(password, hash_password("timing-equalisation-only"))
                raise InvalidCredentials("email or password is incorrect")

            user_id, password_hash, failed, locked_until = row
            if locked_until and locked_until > now:
                raise AccountLocked(
                    f"too many failed attempts; try again after "
                    f"{locked_until.isoformat(timespec='seconds')}"
                )

            if not verify_password(password, password_hash):
                failed += 1
                lock = (now + _dt.timedelta(minutes=_LOCKOUT_MINUTES)
                        if failed >= _MAX_FAILED_ATTEMPTS else None)
                cur.execute(
                    """UPDATE app.user_credential
                          SET failed_attempts = %s, locked_until = %s
                        WHERE user_id = %s""",
                    (failed, lock, user_id),
                )
                self._conn.commit()
                raise InvalidCredentials("email or password is incorrect")

            cur.execute(
                """UPDATE app.user_credential
                      SET failed_attempts = 0, locked_until = NULL, last_login_at = now()
                    WHERE user_id = %s""",
                (user_id,),
            )

            session = self._mint_session(cur, user_id, user_agent)

        self._conn.commit()
        return session

    def _mint_session(self, cur, user_id, user_agent: str | None) -> Session:
        """Insert a session row and return it. Caller owns the transaction.

        Shared by login and password reset: redeeming a reset signs the user in, and
        duplicating the token generation would be one more place for the two paths
        to drift apart on TTL or token length.
        """
        token = secrets.token_urlsafe(_TOKEN_BYTES)
        expires_at = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=SESSION_TTL_HOURS)
        cur.execute(
            """INSERT INTO app.user_session
                   (user_id, token_sha256, expires_at, user_agent)
               VALUES (%s, %s, %s, %s)""",
            (user_id, _token_digest(token), expires_at, user_agent),
        )
        return Session(user_id=str(user_id), token=token, expires_at=expires_at)

    # ----------------------------------------------------------- session --

    def resolve_session(self, token: str) -> str:
        """Return the user_id for a bearer token, or raise SessionExpired."""
        if not token:
            raise SessionExpired("no session token")

        with self._conn.cursor() as cur:
            cur.execute(
                """UPDATE app.user_session
                      SET last_seen_at = now()
                    WHERE token_sha256 = %s
                      AND revoked_at IS NULL
                      AND expires_at > now()
                  RETURNING user_id""",
                (_token_digest(token),),
            )
            row = cur.fetchone()
        self._conn.commit()

        if row is None:
            raise SessionExpired("session is unknown, expired or revoked")
        return str(row[0])

    def logout(self, token: str) -> bool:
        with self._conn.cursor() as cur:
            cur.execute(
                """UPDATE app.user_session SET revoked_at = now()
                    WHERE token_sha256 = %s AND revoked_at IS NULL""",
                (_token_digest(token),),
            )
            revoked = cur.rowcount
        self._conn.commit()
        return revoked > 0

    def logout_everywhere(self, user_id: str) -> int:
        """Revoke every live session. This is why sessions are not JWTs."""
        with self._conn.cursor() as cur:
            cur.execute(
                """UPDATE app.user_session SET revoked_at = now()
                    WHERE user_id = %s AND revoked_at IS NULL""",
                (user_id,),
            )
            revoked = cur.rowcount
        self._conn.commit()
        return revoked

    # ---------------------------------------------------- password reset --

    def create_password_reset(
        self, email: str, *, requested_ip: str | None = None
    ) -> tuple[str, str] | None:
        """Issue a reset token for ``email``, or None when no such account exists.

        Returns ``(user_id, token)``. The caller must NOT tell the requester which it
        got: "if that address has an account, a link is on its way" is the same
        sentence either way, and any variation turns this endpoint into a way to test
        whether someone is a user.

        Requesting a reset invalidates any outstanding one for that account, so an
        attacker who requests a link first cannot keep it alive while the real owner
        requests another.
        """
        normalised = normalise_email(email)
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT user_id FROM app.user_credential WHERE email = %s",
                (normalised,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            user_id = row[0]

            cur.execute(
                """UPDATE app.password_reset SET used_at = now()
                    WHERE user_id = %s AND used_at IS NULL""",
                (user_id,),
            )

            token = secrets.token_urlsafe(_TOKEN_BYTES)
            cur.execute(
                """INSERT INTO app.password_reset
                       (user_id, token_sha256, expires_at, requested_ip)
                   VALUES (%s, %s, now() + %s * interval '1 hour', %s)""",
                (user_id, _token_digest(token), RESET_TTL_HOURS, requested_ip),
            )
        self._conn.commit()
        return (str(user_id), token)

    def reset_password(
        self, token: str, new_password: str, *, user_agent: str | None = None
    ) -> Session:
        """Redeem a reset token, set a new password, and return a fresh session.

        Four things happen together, and the last two are the point of a reset:

        * the token is consumed, so the link in the inbox is spent;
        * the password is replaced;
        * **every live session is revoked** -- if someone else was in the account,
          which is a common reason to reset, letting their session survive would
          make the reset cosmetic;
        * the failed-attempt lockout is cleared, so a user locked out by someone
          guessing at their password can get back in by resetting it. Leaving the
          lock in place would let an attacker deny access to an account simply by
          guessing wrong often enough.
        """
        if not token:
            raise InvalidCredentials("no reset token")
        _validate_password(new_password)

        with self._conn.cursor() as cur:
            cur.execute(
                """UPDATE app.password_reset SET used_at = now()
                    WHERE token_sha256 = %s
                      AND used_at IS NULL
                      AND expires_at > now()
                  RETURNING user_id""",
                (_token_digest(token),),
            )
            row = cur.fetchone()
            if row is None:
                self._conn.rollback()
                raise InvalidCredentials("reset link is unknown, expired or already used")
            user_id = row[0]

            cur.execute(
                """UPDATE app.user_credential
                      SET password_hash = %s, failed_attempts = 0, locked_until = NULL
                    WHERE user_id = %s""",
                (hash_password(new_password), user_id),
            )
            cur.execute(
                """UPDATE app.user_session SET revoked_at = now()
                    WHERE user_id = %s AND revoked_at IS NULL""",
                (user_id,),
            )
            # Minted AFTER the revoke, so the session handed back is the only live
            # one. Minting first would revoke it immediately.
            session = self._mint_session(cur, user_id, user_agent)
        self._conn.commit()
        return session

    # ----------------------------------------------------------- consent --

    def has_consent(self, user_id: str, purpose_key: str) -> bool:
        with self._conn.cursor() as cur:
            cur.execute(
                """SELECT 1
                     FROM app.user_consent c
                     JOIN app.consent_purpose p USING (purpose_id)
                    WHERE c.user_id = %s AND p.purpose_key = %s
                      AND c.withdrawn_at IS NULL
                    LIMIT 1""",
                (user_id, purpose_key),
            )
            return cur.fetchone() is not None

    def grant_consent(
        self, user_id: str, purpose_key: str, *,
        notice_version: str = "v1", notice_lang: str = "en",
        evidence: dict[str, Any] | None = None,
    ) -> None:
        import json

        with self._conn.cursor() as cur:
            # Granting consent is itself the act that establishes an unregistered
            # user, so the row has to exist for the foreign key. Otherwise a
            # development-identity caller could never consent to anything: writes
            # need consent, and the user row was only created by a write.
            cur.execute(
                """INSERT INTO app.app_user (user_id, preferred_lang, is_anonymised)
                   VALUES (%s, 'en', true) ON CONFLICT (user_id) DO NOTHING""",
                (user_id,),
            )
            # Idempotent: user_consent_active_idx is UNIQUE on (user_id,
            # purpose_id) while active, so re-granting a live consent would raise.
            # A client tapping the same toggle twice is not an error, and a 500 on
            # a consent screen is a good way to make someone give up on granting it.
            cur.execute(
                """INSERT INTO app.user_consent
                       (user_id, purpose_id, notice_version, notice_lang, evidence)
                   SELECT %s, p.purpose_id, %s, %s, %s
                     FROM app.consent_purpose p
                    WHERE p.purpose_key = %s
                      AND NOT EXISTS (
                            SELECT 1 FROM app.user_consent c
                             WHERE c.user_id = %s AND c.purpose_id = p.purpose_id
                               AND c.withdrawn_at IS NULL
                      )""",
                (user_id, notice_version, notice_lang,
                 json.dumps(evidence or {}), purpose_key, user_id),
            )
        self._conn.commit()
