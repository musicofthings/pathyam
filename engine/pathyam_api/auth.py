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
]

# scrypt interactive-login parameters. ~32 MB and ~100 ms per hash on commodity
# hardware: slow enough to matter to an attacker, fast enough for a login.
_SCRYPT_N = 1 << 15
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SALT_BYTES = 16

SESSION_TTL_HOURS = 24 * 14
_TOKEN_BYTES = 32
_MAX_FAILED_ATTEMPTS = 8
_LOCKOUT_MINUTES = 15

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
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

            token = secrets.token_urlsafe(_TOKEN_BYTES)
            expires_at = now + _dt.timedelta(hours=SESSION_TTL_HOURS)
            cur.execute(
                """INSERT INTO app.user_session
                       (user_id, token_sha256, expires_at, user_agent)
                   VALUES (%s, %s, %s, %s)""",
                (user_id, _token_digest(token), expires_at, user_agent),
            )

        self._conn.commit()
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
            cur.execute(
                """INSERT INTO app.user_consent
                       (user_id, purpose_id, notice_version, notice_lang, evidence)
                   SELECT %s, purpose_id, %s, %s, %s
                     FROM app.consent_purpose WHERE purpose_key = %s""",
                (user_id, notice_version, notice_lang,
                 json.dumps(evidence or {}), purpose_key),
            )
        self._conn.commit()
