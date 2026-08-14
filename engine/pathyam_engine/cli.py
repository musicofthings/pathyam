"""Command-line interface: compute a template against a live database.

    python -m pathyam_engine compute PY-T-000101 --region KA \\
        --set fat_g=11 --set fat_type=ghee --samples 5000

    python -m pathyam_engine compute PY-T-000101 --json > result.json

Reads the DSN from ``--dsn`` or the ``PATHYAM_DSN`` environment variable.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from .engine import ENGINE_VERSION, ComputeEngine
from .repository import PostgresRepository

_STATUS_MARK = {"PASS": "ok", "WARN": "warn", "FAIL": "FAIL", "SKIP": "--"}


def _coerce(value: str) -> Any:
    """Numbers stay numbers; everything else is a category label."""
    try:
        return float(value)
    except ValueError:
        return value


def _parse_sets(pairs: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"--set expects name=value, got {pair!r}")
        name, _, value = pair.partition("=")
        out[name.strip()] = _coerce(value.strip())
    return out


def _render(result) -> str:
    lines: list[str] = []
    add = lines.append

    add("=" * 74)
    add(f"  {result.food_name}   [{result.template_pathyam_id}]")
    add("=" * 74)
    add(f"  engine {result.engine_version} · {result.n_samples} samples · seed {result.seed}")
    add("")
    add(f"  {result.summary_line()}")
    add("")

    add("  NUTRIENTS (per serving, 80% credible interval)")
    add("  " + "-" * 70)
    add(f"  {'nutrient':<26}{'p50':>10}{'p10':>10}{'p90':>10}{'unit':>8}{'tier':>6}")
    for tag, n in result.nutrients.items():
        s = n.per_serving
        add(f"  {n.name[:25]:<26}{s.p50:>10.2f}{s.p10:>10.2f}{s.p90:>10.2f}"
            f"{n.unit:>8}{n.worst_confidence:>6}")
    add("")

    add("  INGREDIENTS (grams, median)")
    add("  " + "-" * 70)
    for row in result.ingredients:
        via = f"  via {row['via_sub_template']}" if row["via_sub_template"] else ""
        g = row["grams"]
        # A median of 0 with a positive p90 means a categorical selector picks this
        # branch only sometimes (e.g. raw rice when rice_type is usually parboiled).
        # Printing a bare "0.0 g" makes that look like a bug rather than a distribution.
        if g["p50"] == 0 and g["p90"] > 0:
            add(f"  {row['food_name'][:40]:<42}{'  sometimes':>10}"
                f"  (0-{g['p90']:.1f} g){via}")
        else:
            add(f"  {row['food_name'][:40]:<42}{g['p50']:>8.1f} g{via}")
    add(f"  {'raw total':<42}{result.raw_mass_g.p50:>8.1f} g")
    add(f"  {'cooked total':<42}{result.cooked_mass_g.p50:>8.1f} g")
    add("")

    if result.variance_contributions:
        add("  WHERE THE UNCERTAINTY COMES FROM (first-order)")
        add("  " + "-" * 70)
        for name, share in list(result.variance_contributions.items())[:6]:
            bar = "#" * int(round(share * 40))
            add(f"  {name:<26}{share * 100:>6.1f}%  {bar}")
        add("")

    add("  QUALITY CONTROL")
    add("  " + "-" * 70)
    for q in result.qc:
        add(f"  [{_STATUS_MARK.get(q.status, q.status):>4}] {q.gate:<24} {q.message}")
    add("")

    add("  SOURCES")
    add("  " + "-" * 70)
    for s in result.sources:
        flag = "" if s.is_commercial_cleared else "   <-- NOT CLEARED FOR COMMERCIAL USE"
        add(f"  {s.source_key:<20} {s.licence[:40]}{flag}")
    add("")

    if result.warnings:
        add("  WARNINGS")
        add("  " + "-" * 70)
        for w in result.warnings:
            add(f"  ! {w}")
        add("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pathyam_engine",
        description="Deterministic nutrient computation over parametric recipe templates.",
    )
    parser.add_argument("--version", action="version", version=f"pathyam-engine {ENGINE_VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    c = sub.add_parser("compute", help="compute nutrients for one template")
    c.add_argument("template", help="template pathyam_id, e.g. PY-T-000101")
    c.add_argument("--dsn", default=os.environ.get("PATHYAM_DSN"),
                   help="Postgres DSN (or set PATHYAM_DSN)")
    c.add_argument("--region", default=None, help="region key, e.g. KL, TN, KA")
    c.add_argument("--set", action="append", default=[], metavar="NAME=VALUE",
                   help="pin a parameter (repeatable); treated as user-stated")
    c.add_argument("--samples", type=int, default=2000)
    c.add_argument("--servings", type=float, default=None)
    c.add_argument("--seed", type=int, default=None)
    c.add_argument("--min-confidence", default="D", choices=list("ABCD"))
    c.add_argument("--json", action="store_true", help="emit JSON instead of a report")

    args = parser.parse_args(argv)

    if not args.dsn:
        parser.error("no DSN: pass --dsn or set PATHYAM_DSN")

    try:
        import psycopg
    except ImportError:  # pragma: no cover
        parser.error("psycopg is required for the CLI: pip install 'psycopg[binary]'")

    with psycopg.connect(args.dsn) as conn:
        engine = ComputeEngine(PostgresRepository(conn))
        result = engine.compute(
            args.template,
            n_samples=args.samples,
            region_key=args.region,
            param_overrides=_parse_sets(args.set),
            servings=args.servings,
            seed=args.seed,
            min_confidence=args.min_confidence,
        )

    if args.json:
        json.dump(result.as_dict(), sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
    else:
        print(_render(result))

    # Non-zero exit on a hard QC failure so CI and batch jobs notice.
    return 1 if any(q.status == "FAIL" for q in result.qc) else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
