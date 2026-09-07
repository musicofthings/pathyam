#!/usr/bin/env bash
# Pathyam — local development launcher.
#
#   ./dev.sh              set up if needed, then serve on http://127.0.0.1:8000
#   ./dev.sh --reset      wipe the local database and rebuild from scratch
#   ./dev.sh --no-serve   set up and load only, do not start the server
#
# Uses `pgserver`, which ships a prebuilt PostgreSQL 16 and runs it as your own user
# — no Homebrew, no Docker, no root, no port conflict with anything else installed.
set -euo pipefail

cd "$(dirname "$0")"

VENV=".venv"
PORT="${PORT:-8000}"
RESET=0
SERVE=1

# The Postgres data directory lives OUTSIDE the project folder, under $HOME.
#
# PostgreSQL puts its Unix socket in the data directory, and socket files cannot be
# created on iCloud Drive, Dropbox, network shares or FUSE mounts — the server dies
# with "could not set permissions of file .s.PGSQL.5432: Invalid argument". Since
# this repo may well sit in a synced folder, keeping pgdata on local disk avoids a
# confusing failure that has nothing to do with the application.
#
# Override with PATHYAM_PGDATA if you want it elsewhere.
PGDATA_DIR="${PATHYAM_PGDATA:-$HOME/.pathyam/pg}"
export PATHYAM_PGDATA="$PGDATA_DIR"

for arg in "$@"; do
  case "$arg" in
    --reset)    RESET=1 ;;
    --no-serve) SERVE=0 ;;
    -h|--help)  sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

step() { printf '\n\033[1m▸ %s\033[0m\n' "$1"; }

# ---------------------------------------------------------------- python ----
if ! command -v python3 >/dev/null; then
  echo "python3 not found. Install Python 3.10 or newer." >&2; exit 1
fi
PYV=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'; then
  echo "Python 3.10+ required, found $PYV." >&2; exit 1
fi

if [[ ! -d "$VENV" ]]; then
  step "creating virtualenv ($VENV, python $PYV)"
  python3 -m venv "$VENV"
fi
PY="$VENV/bin/python"

if [[ ! -f "$VENV/.deps-ok" ]]; then
  step "installing dependencies (one time, ~1 min)"
  "$PY" -m pip install --quiet --upgrade pip
  "$PY" -m pip install --quiet \
      numpy pyyaml "psycopg[binary]" psycopg_pool pgserver \
      fastapi "uvicorn[standard]" httpx pytest
  touch "$VENV/.deps-ok"
fi

if [[ $RESET -eq 1 ]]; then
  step "resetting local database ($PGDATA_DIR)"
  "$PY" -c "
import os, pathlib, pgserver
d = pathlib.Path(os.environ['PATHYAM_PGDATA'])
if d.exists():
    try: pgserver.get_server(str(d)).cleanup()
    except Exception: pass
" || true
  rm -rf "$PGDATA_DIR"
fi

mkdir -p "$PGDATA_DIR" .devdata

# ------------------------------------------------- database, schema, data ----
step "starting PostgreSQL and applying schema"
"$PY" scripts/dev_setup.py

DSN="$(cat .devdata/dsn)"
export PATHYAM_DSN="$DSN"
export PYTHONPATH=.

step "fetching IFCT 2017 composition tables"
"$PY" scripts/fetch_ifct.py

step "ingesting IFCT 2017 composition (528 foods)"
"$PY" -m pathyam_engine.authoring ifct

step "loading recipe templates"
"$PY" -m pathyam_engine.authoring --dir ../db/templates validate
"$PY" -m pathyam_engine.authoring --dir ../db/templates load >/dev/null
step "filling composition for foods absent from IFCT 2017"
"$PY" -m pathyam_engine.authoring derived

step "auditing template computability"
"$PY" -m pathyam_engine.authoring --dir ../db/templates audit --worklist 6 | head -30

if [[ $SERVE -eq 0 ]]; then
  step "setup complete (--no-serve)"
  echo "   export PATHYAM_DSN='$DSN'"
  exit 0
fi

step "serving on http://127.0.0.1:$PORT"
cat <<BANNER
   test page   http://127.0.0.1:$PORT/
   API docs    http://127.0.0.1:$PORT/docs
   health      http://127.0.0.1:$PORT/v1/health

   ⚠ No authentication, no rate limiting, no request logging.
     Bound to 127.0.0.1 deliberately. Do not expose this.

   Ctrl-C to stop. Data persists in $PGDATA_DIR between runs.

BANNER

exec "$PY" scripts/dev_serve.py "$PORT"
