#!/usr/bin/env python3
"""Start the embedded PostgreSQL and apply migrations if the schema is absent.

Called by dev.sh. Separate from the shell script because nesting a Python heredoc
inside a bash heredoc is a reliable way to produce confusing quoting bugs.
"""

from __future__ import annotations

import os
import pathlib
import sys

import pgserver

DB_DIR = pathlib.Path(__file__).resolve().parents[2] / "db"


def main() -> int:
    pgdata = pathlib.Path(os.environ["PATHYAM_PGDATA"])
    pgdata.mkdir(parents=True, exist_ok=True)

    try:
        # cleanup_mode=None leaves the cluster running after this process exits, so the
        # template loader and then the API server can connect to it. With the default
        # 'stop', the database vanishes the moment setup finishes.
        db = pgserver.get_server(str(pgdata), cleanup_mode=None)
    except Exception as exc:                                    # noqa: BLE001
        print(f"\n   could not start PostgreSQL in {pgdata}", file=sys.stderr)
        if "could not set permissions" in str(exc) or "Unix-domain socket" in str(exc):
            print(
                "   That directory cannot host a Unix socket — it is probably on\n"
                "   iCloud Drive, Dropbox or a network share. Set PATHYAM_PGDATA to a\n"
                "   path on local disk, e.g.\n\n"
                "       PATHYAM_PGDATA=/tmp/pathyam-pg ./dev.sh\n",
                file=sys.stderr,
            )
        else:
            print(f"   {exc}", file=sys.stderr)
        return 1

    uri = db.get_uri()
    devdata = pathlib.Path(".devdata")
    devdata.mkdir(exist_ok=True)
    (devdata / "dsn").write_text(uri, encoding="utf-8")

    # Migrations use plain CREATE TABLE, so they are not idempotent. Apply once.
    tables = db.psql("SELECT tablename FROM pg_tables WHERE schemaname = 'ref';")
    if "recipe_template" in tables:
        print("   schema present, skipping migrations (use --reset to rebuild)")
    else:
        for path in sorted(DB_DIR.glob("0*.sql")):
            if path.name.startswith("011"):
                # pg_trgm ships in contrib and is absent from this build; the
                # resolver degrades to Python-side scoring with identical ranking.
                continue
            db.psql(f"\\set ON_ERROR_STOP on\n\\i {path}\n")
            print(f"   applied {path.name}")

    print(f"   pgdata: {pgdata}")
    print(f"   dsn:    {uri}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
