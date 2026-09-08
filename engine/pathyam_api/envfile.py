"""Load a ``.env`` file into ``os.environ``.

WHY THIS EXISTS
---------------
`.env.example` documented OPENROUTER_API_KEY, SMTP_HOST, ALLOWED_ORIGINS and the
rest as though copying it to `.env` would configure the app. Nothing read that file:
every consumer calls ``os.environ.get`` directly, and python-dotenv was never a
dependency. So the documented setup silently did nothing, and the failure looked
like a bad key rather than a key that was never loaded.

Stdlib only, ~30 lines, consistent with the engine depending on nothing but numpy.

PRECEDENCE
----------
A variable already in the environment WINS. The file is a convenience for local
development; an explicitly exported value, a systemd unit, or a container's env
must never be silently overridden by a stale file in the working directory.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["load_env_file"]


def load_env_file(path: str | Path | None = None) -> dict[str, str]:
    """Read ``path`` (default: nearest ``.env``) into os.environ. Returns what it set.

    Missing file is not an error -- production configures the environment directly
    and has no .env to read.
    """
    if path is None:
        here = Path(__file__).resolve().parent
        for candidate in (here.parent / ".env", here.parent.parent / ".env"):
            if candidate.is_file():
                path = candidate
                break
        else:
            return {}

    path = Path(path)
    if not path.is_file():
        return {}

    applied: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip()
        # Strip one layer of matching quotes; a value with spaces is commonly quoted.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if not key or key in os.environ:
            continue
        os.environ[key] = value
        applied[key] = value
    return applied
