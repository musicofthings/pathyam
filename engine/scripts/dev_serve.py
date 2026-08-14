#!/usr/bin/env python3
"""Run the API with the embedded PostgreSQL held open for the server's lifetime.

pgserver stops the cluster when the process that started it exits, so the server
process itself has to own the handle — otherwise the database disappears the moment
setup finishes.
"""

from __future__ import annotations

import os
import pathlib
import sys

import pgserver
import uvicorn


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    db = pgserver.get_server(str(pathlib.Path(os.environ["PATHYAM_PGDATA"])), cleanup_mode=None)
    os.environ["PATHYAM_DSN"] = db.get_uri()
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    uvicorn.run("pathyam_api.main:app", host="127.0.0.1", port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
