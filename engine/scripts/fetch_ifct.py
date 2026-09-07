#!/usr/bin/env python3
"""Fetch the machine-readable IFCT 2017 tables into a local, gitignored cache.

    python3 scripts/fetch_ifct.py            # fetch if missing
    python3 scripts/fetch_ifct.py --force    # re-fetch

WHY THIS IS A FETCH AND NOT A VENDORED FILE
-------------------------------------------
The data is ICMR-NIN's Indian Food Composition Tables 2017. The npm packages that
carry it (`@ifct2017/*`, by Subhajit Sahu) are MIT licensed, but that licence
covers the *packaging code* -- the underlying table is ICMR-NIN copyright and is
free-to-read, not established as free to redistribute commercially (dossier risk
R1). So this repository does not carry a copy: it fetches one at setup time into
`.ifctdata/`, which is gitignored.

Everything loaded from here lands with `is_commercial_cleared = false` on the
source row, so it shows up in `ref.v_uncleared_values` -- the release gate -- until
written ICMR-NIN permission exists.

    Longvah T, Ananthan R, Bhaskarachary K, Venkaiah K.
    Indian Food Composition Tables 2017. ICMR-NIN, Hyderabad.
    https://www.nin.res.in/ebooks/IFCT2017.pdf
"""

from __future__ import annotations

import argparse
import io
import sys
import tarfile
import urllib.request
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent.parent / ".ifctdata"

# Pinned versions. Bumping these changes every composition value in the database,
# so it is a deliberate act -- re-run the audit and diff the QC report afterwards.
PACKAGES = {
    "compositions": ("@ifct2017/compositions", "2.0.9"),
    "columns": ("@ifct2017/columns", "2.0.13"),
}

_REGISTRY = "https://registry.npmjs.org"


def _tarball_url(package: str, version: str) -> str:
    # @scope/name -> https://registry.npmjs.org/@scope/name/-/name-version.tgz
    bare = package.split("/")[-1]
    return f"{_REGISTRY}/{package}/-/{bare}-{version}.tgz"


def fetch_one(name: str, package: str, version: str, *, force: bool) -> Path:
    target = CACHE_DIR / f"{name}.csv"
    if target.exists() and not force:
        print(f"  {name:<14} cached ({target.stat().st_size:,} bytes)")
        return target

    url = _tarball_url(package, version)
    print(f"  {name:<14} fetching {package}@{version}")
    with urllib.request.urlopen(url, timeout=60) as resp:
        payload = resp.read()

    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tar:
        member = tar.getmember("package/index.csv")
        extracted = tar.extractfile(member)
        if extracted is None:
            raise SystemExit(f"{package}: package/index.csv is not a regular file")
        data = extracted.read()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    print(f"  {name:<14} wrote {target} ({len(data):,} bytes)")
    return target


def fetch_all(*, force: bool = False) -> dict[str, Path]:
    print(f"IFCT 2017 tables -> {CACHE_DIR}")
    paths = {
        name: fetch_one(name, pkg, ver, force=force)
        for name, (pkg, ver) in PACKAGES.items()
    }
    print("\n  Source: ICMR-NIN IFCT 2017. Commercial reuse rights NOT established.")
    print("  Loaded values stay visible in ref.v_uncleared_values until they are.")
    return paths


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="re-fetch even if cached")
    args = ap.parse_args(argv)
    try:
        fetch_all(force=args.force)
    except Exception as exc:
        print(f"fetch failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
