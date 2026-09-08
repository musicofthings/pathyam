"""Outbound mail.

Password reset is the first thing here that has to reach a person, and it is why
this module exists: item 10 of the follow-up was blocked on "needs somewhere to send
mail". Deliberately small -- stdlib ``smtplib``, no dependency, no queue.

TWO BACKENDS, AND A PRODUCTION GUARD
------------------------------------
``ConsoleMailer`` writes the message to the log and sends nothing. That is the right
default for development, where there is no SMTP server and a reset link in the log is
exactly what a developer needs.

It is also a security hole if it ever runs in production: the reset token would be
written to a log file instead of reaching the user, and anyone with log access could
take over any account. So :func:`build_mailer` refuses to return it when
``PATHYAM_ENV=production`` and no SMTP host is configured. A misconfigured deployment
fails at startup rather than quietly logging credentials -- the same choice the CORS
config makes by refusing to fall back to a wildcard.

WHAT THIS IS NOT
----------------
There is no retry, no queue and no bounce handling. Send failures raise, and the
caller decides. For password reset the caller deliberately does NOT surface the
failure to the requester, because doing so would leak whether the address exists.
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from typing import Protocol

__all__ = ["Mailer", "ConsoleMailer", "SMTPMailer", "MailerNotConfigured", "build_mailer"]

log = logging.getLogger("pathyam.mail")


class MailerNotConfigured(RuntimeError):
    """No usable transport. A configuration problem, not a transient failure."""


class Mailer(Protocol):
    def send(self, *, to: str, subject: str, body: str) -> None: ...


class ConsoleMailer:
    """Development only. Writes the message to the log and sends nothing.

    Never selected in production -- see :func:`build_mailer`.
    """

    #: Read by tests and by the dev UI so a reset can be completed offline.
    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []

    def send(self, *, to: str, subject: str, body: str) -> None:
        self.sent.append({"to": to, "subject": subject, "body": body})
        log.warning(
            "ConsoleMailer: no mail was sent. Message follows.\n"
            "  To: %s\n  Subject: %s\n%s", to, subject, body,
        )


class SMTPMailer:
    """Plain stdlib SMTP with STARTTLS."""

    def __init__(
        self,
        host: str,
        port: int = 587,
        *,
        username: str | None = None,
        password: str | None = None,
        sender: str = "no-reply@pathyam.app",
        use_tls: bool = True,
        timeout: float = 15.0,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.sender = sender
        self.use_tls = use_tls
        self.timeout = timeout

    def send(self, *, to: str, subject: str, body: str) -> None:
        message = EmailMessage()
        message["From"] = self.sender
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)

        with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as smtp:
            if self.use_tls:
                smtp.starttls(context=ssl.create_default_context())
            if self.username:
                smtp.login(self.username, self.password or "")
            smtp.send_message(message)


def build_mailer() -> Mailer:
    """Choose a transport from the environment.

    SMTP when ``SMTP_HOST`` is set. Otherwise the console mailer in development, and
    a hard failure in production: writing a password reset token to a log file where
    it reaches an operator instead of the user is worse than not offering reset.
    """
    host = os.environ.get("SMTP_HOST", "").strip()
    if host:
        return SMTPMailer(
            host,
            int(os.environ.get("SMTP_PORT", "587")),
            username=os.environ.get("SMTP_USERNAME") or None,
            password=os.environ.get("SMTP_PASSWORD") or None,
            sender=os.environ.get("MAIL_FROM", "no-reply@pathyam.app"),
            use_tls=os.environ.get("SMTP_TLS", "1").strip() not in ("0", "false", "False"),
        )

    if os.environ.get("PATHYAM_ENV", "development") == "production":
        raise MailerNotConfigured(
            "SMTP_HOST is not set and PATHYAM_ENV=production. The console mailer "
            "writes reset tokens to the log instead of sending them, which would let "
            "anyone with log access take over any account. Configure SMTP."
        )
    return ConsoleMailer()
