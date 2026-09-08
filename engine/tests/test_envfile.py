"""Loading .env — the file .env.example told you to create, which nothing read."""

from __future__ import annotations

import os

from pathyam_api.envfile import load_env_file


def test_a_dotenv_file_reaches_os_environ(tmp_path, monkeypatch):
    """The bug: OPENROUTER_API_KEY in .env had no effect, and the failure looked
    like a bad key rather than a key that was never loaded."""
    env = tmp_path / ".env"
    env.write_text("OPENROUTER_API_KEY=test-value-not-a-real-key\nSMTP_PORT=2525\n")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("SMTP_PORT", raising=False)

    applied = load_env_file(env)

    assert applied["OPENROUTER_API_KEY"] == "test-value-not-a-real-key"
    assert os.environ["SMTP_PORT"] == "2525"


def test_an_existing_environment_variable_wins(tmp_path, monkeypatch):
    """A stale file in the working directory must not override a deliberate export,
    a systemd unit, or a container's environment."""
    env = tmp_path / ".env"
    env.write_text("PATHYAM_ENV=development\n")
    monkeypatch.setenv("PATHYAM_ENV", "production")

    load_env_file(env)

    assert os.environ["PATHYAM_ENV"] == "production"


def test_comments_blanks_quotes_and_export_prefixes(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "# a comment\n"
        "\n"
        'MAIL_FROM="no-reply@pathyam.app"\n'
        "export PATHYAM_VISION_MODEL=auto:free\n"
        "ALLOWED_ORIGINS='http://a,http://b'\n"
        "NOT_A_PAIR\n"
    )
    for key in ("MAIL_FROM", "PATHYAM_VISION_MODEL", "ALLOWED_ORIGINS"):
        monkeypatch.delenv(key, raising=False)

    applied = load_env_file(env)

    assert applied["MAIL_FROM"] == "no-reply@pathyam.app"
    assert applied["PATHYAM_VISION_MODEL"] == "auto:free"
    assert applied["ALLOWED_ORIGINS"] == "http://a,http://b"
    assert "NOT_A_PAIR" not in applied


def test_a_missing_file_is_not_an_error(tmp_path):
    """Production configures the environment directly and has no .env."""
    assert load_env_file(tmp_path / "nope.env") == {}
