#!/usr/bin/env bash
# Full test run: unit tests, then integration tests against a throwaway Postgres
# seeded from ../db. Uses pgserver, so no root and no Docker required.
#
#   ./run_tests.sh          unit + integration
#   ./run_tests.sh --unit   unit only (no database)
set -euo pipefail

cd "$(dirname "$0")"
export PYTHONPATH=.

PYTHON_BIN="python3"
if [[ -x ".venv/bin/python" ]]; then
    PYTHON_BIN=".venv/bin/python"
fi

echo "=== unit tests ==="
"$PYTHON_BIN" -m pytest tests/ -q \
    --ignore=tests/test_integration_postgres.py \
    --ignore=tests/test_api.py \
    --deselect tests/test_authoring.py::test_load_and_audit_against_postgres \
    --deselect tests/test_authoring.py::test_computable_templates_land_in_a_plausible_energy_band

if [[ "${1:-}" == "--unit" ]]; then
    exit 0
fi

echo
echo "=== integration + API tests (throwaway Postgres, seeded from ../db) ==="
"$PYTHON_BIN" - <<'PY'
import os, pathlib, subprocess, sys, tempfile
try:
    import pgserver
except ImportError:
    sys.exit("pgserver not installed: pip install pgserver")

pgdata = pathlib.Path(tempfile.mkdtemp(prefix="pathyam-pg-"))
db = pgserver.get_server(str(pgdata))

sql_dir = pathlib.Path("../db").resolve()
for f in sorted(sql_dir.glob("0*.sql")):
    if f.name.startswith("011"):
        continue  # needs pg_trgm from contrib; optional
    db.psql(f"\\set ON_ERROR_STOP on\n\\i {f}\n")
print(f"seeded {pgdata}")

# pgserver stops the cluster when this process exits, so run pytest as a child.
env = dict(os.environ, PYTHONPATH=".", PATHYAM_TEST_DSN=db.get_uri())
sys.exit(subprocess.call(
    [sys.executable, "-m", "pytest",
     "tests/test_integration_postgres.py", "tests/test_api.py",
     "tests/test_authoring.py", "-q"],
    env=env))
PY
