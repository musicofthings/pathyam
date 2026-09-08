#!/usr/bin/env python3
"""Fetch the CGMacros dataset into a local, gitignored cache.

    python3 scripts/fetch_cgmacros.py            # fetch if missing
    python3 scripts/fetch_cgmacros.py --force    # re-fetch
    python3 scripts/fetch_cgmacros.py --check    # print licence and cohort, fetch nothing

WHY THIS IS A FETCH AND NOT A VENDORED FILE
-------------------------------------------
CGMacros is licensed **CC BY-NC-SA 4.0**. Three consequences, all of which outlive
this script:

  NonCommercial  Pathyam is a commercial product. This data cannot ship in one.
  ShareAlike     Adaptations must carry the same licence. A model fitted on these
                 traces is arguably an adaptation, which would make the fitted
                 coefficients themselves CC BY-NC-SA.
  Attribution    Any use must credit the authors.

So the repository carries no copy: it fetches one at setup time into `.cgmacros/`,
which is gitignored, and the ingest registers the source with
`is_commercial_cleared = false` so it appears in `ref.v_release_blockers`.

WHAT THIS DATA IS AND IS NOT FOR
--------------------------------
It is for exercising the postprandial pipeline -- ingestion, meal/reading pairing,
and the shape of a fitting routine -- against real traces instead of none.

It is **not** for fitting the coefficients that ship. The cohort was recruited at
the Sansum Diabetes Research Institute in Santa Barbara, California: 34 of 45
participants self-identified as Hispanic/Latino, 7 White, 4 African American, and
none as South Asian. Breakfasts were protein shakes; lunches were ordered from a
Mexican restaurant chain. There is no South Indian food in it and no South Asian
metabolism behind it, and postprandial glycaemic response transfers across neither.

Nor does it substitute for the golden meal dataset. Its portion field is
"Amount Consumed -- Estimate of % of meal consumed", read off before/after
photographs. Nothing was weighed.

    Das S, Kerr D, et al. CGMacros: a pilot scientific dataset for personalized
    nutrition and diet monitoring. Scientific Data (2025).
    https://physionet.org/content/cgmacros/1.0.0/
"""

from __future__ import annotations

import argparse
import shutil
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cgmacros"

BASE = "https://physionet.org/files/cgmacros/1.0.0"
ARCHIVE = "CGMacros_dateshifted365.zip"
ARCHIVE_URL = f"{BASE}/{ARCHIVE}"

LICENCE = "CC BY-NC-SA 4.0"
CITATION = (
    "Das S, Kerr D, et al. CGMacros: a pilot scientific dataset for personalized "
    "nutrition and diet monitoring. Scientific Data (2025). PhysioNet v1.0.0."
)
POPULATION_NOTE = (
    "45 adults recruited at Sansum Diabetes Research Institute, Santa Barbara, "
    "California, 2021-2024 (15 normoglycaemic, 16 prediabetes, 14 type 2 diabetes). "
    "Self-identified ethnicity: 34 Hispanic/Latino, 7 White, 4 African American; "
    "none South Asian. Standardised breakfasts were protein shakes and lunches were "
    "ordered from a Mexican restaurant chain; dinners were self-selected. No South "
    "Indian food. Portions were estimated from before/after photographs as a "
    "percentage consumed, not weighed. Timestamps are randomly date-shifted."
)

# ~627 MB. Worth saying out loud before it starts.
_APPROX_MB = 627


def _banner() -> None:
    print(f"CGMacros — {LICENCE}", file=sys.stderr)
    print(f"  {CITATION}", file=sys.stderr)
    print(f"  Cohort: {POPULATION_NOTE}", file=sys.stderr)
    print(
        "\n  NonCommercial. This data cannot ship in a commercial product, and a\n"
        "  model fitted on it may inherit ShareAlike. The ingest registers it as\n"
        "  uncleared so it appears in ref.v_release_blockers.\n",
        file=sys.stderr,
    )


def fetch(force: bool = False) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    archive = CACHE_DIR / ARCHIVE
    marker = CACHE_DIR / ".extracted"

    if marker.exists() and not force:
        print(f"already present in {CACHE_DIR}", file=sys.stderr)
        return CACHE_DIR

    if force and marker.exists():
        marker.unlink()

    if not archive.exists() or force:
        print(f"fetching {ARCHIVE_URL} (~{_APPROX_MB} MB)...", file=sys.stderr)
        try:
            with urllib.request.urlopen(ARCHIVE_URL, timeout=120) as resp, \
                 archive.open("wb") as out:
                shutil.copyfileobj(resp, out)
        except (urllib.error.URLError, OSError) as exc:
            archive.unlink(missing_ok=True)
            raise SystemExit(f"could not fetch CGMacros: {exc}") from exc

    print("extracting...", file=sys.stderr)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(CACHE_DIR)
    marker.write_text(f"{ARCHIVE_URL}\n{LICENCE}\n")
    print(f"ready: {CACHE_DIR}", file=sys.stderr)
    return CACHE_DIR


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fetch_cgmacros.py")
    parser.add_argument("--force", action="store_true", help="re-fetch and re-extract")
    parser.add_argument("--check", action="store_true",
                        help="print licence and cohort, fetch nothing")
    args = parser.parse_args(argv)

    _banner()
    if args.check:
        return 0
    fetch(force=args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
