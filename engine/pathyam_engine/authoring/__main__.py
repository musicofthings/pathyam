"""CLI for the template authoring pipeline."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .audit import audit as run_audit
from .lexicon import load_lexicon_file, load_lexicon_into_postgres
from .loader import load_into_postgres
from .schema import load_library

_DEFAULT_DIR = Path(__file__).resolve().parents[3].parent / "db" / "templates"


def _resolve_dir(explicit: Path | None) -> Path:
    if explicit:
        return explicit
    for candidate in (_DEFAULT_DIR, Path("db/templates"), Path("../db/templates")):
        if candidate.exists():
            return candidate
    raise SystemExit("could not locate db/templates; pass --dir")


def _load(directory: Path):
    ingredients = directory / "ingredients.yaml"
    templates = sorted(p for p in directory.glob("*.yaml") if p.name != "ingredients.yaml")
    if not ingredients.exists():
        raise SystemExit(f"{ingredients} not found")
    return load_library(ingredients, templates)


def _print_issues(library) -> None:
    for issue in library.issues:
        print(f"  {issue}")
    print()
    print(f"  {len(library.errors)} error(s), {len(library.warnings)} warning(s), "
          f"{len(library.templates)} template(s), {len(library.ingredients)} ingredient(s)")


def cmd_validate(args) -> int:
    library = _load(_resolve_dir(args.dir))
    if args.json:
        json.dump({"errors": [str(i) for i in library.errors],
                   "warnings": [str(i) for i in library.warnings],
                   "templates": len(library.templates)},
                  sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0 if library.ok else 1

    print("=" * 78)
    print("  TEMPLATE LIBRARY VALIDATION")
    print("=" * 78)
    _print_issues(library)
    return 0 if library.ok else 1


def cmd_load(args) -> int:
    import psycopg

    library = _load(_resolve_dir(args.dir))
    if not library.ok:
        print("refusing to load: library has validation errors")
        _print_issues(library)
        return 1

    dishes = {}
    if args.lexicon and Path(args.lexicon).exists():
        dishes = load_lexicon_file(args.lexicon)

    with psycopg.connect(args.dsn) as conn:
        result = load_into_postgres(
            library, conn, dry_run=args.dry_run,
            dish_names={k: d.en for k, d in dishes.items()},
        )
        lex = None
        if dishes and not args.dry_run:
            lex = load_lexicon_into_postgres(dishes, conn)

    payload = result.as_dict()
    if lex:
        payload["lexicon"] = lex.as_dict()
    print(json.dumps(payload, indent=2))
    if args.dry_run:
        print("\n(dry run — rolled back)")
    return 0


def cmd_audit(args) -> int:
    import psycopg

    from ..repository import PostgresRepository

    library = _load(_resolve_dir(args.dir))
    with psycopg.connect(args.dsn) as conn:
        report = run_audit(library, PostgresRepository(conn), n_samples=args.samples)

    if args.json:
        json.dump(report.as_dict(), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    print("=" * 78)
    print("  TEMPLATE AUDIT")
    print("=" * 78)
    print(f"  {len(report.statuses)} templates · {len(report.computable)} computable "
          f"· {len(report.blocked)} blocked · coverage {report.coverage * 100:.0f}%")
    if report.library_errors:
        print(f"  ⚠ {report.library_errors} validation error(s) — run `validate`")
    print()

    if report.computable:
        print("  COMPUTABLE")
        print("  " + "-" * 74)
        print(f"  {'template':<16}{'dish':<20}{'kcal/serv':>10}{'kcal/100g':>11}"
              f"{'±width':>8}  dominant")
        for s in report.computable:
            width = f"{s.interval_width * 100:.0f}%" if s.interval_width else "-"
            print(f"  {s.template_id:<16}{s.dish[:19]:<20}"
                  f"{s.energy_per_serving or 0:>10.0f}{s.energy_per_100g or 0:>11.0f}"
                  f"{width:>8}  {s.dominant_param or '-'}")
            for failure in s.qc_failures:
                print(f"      ⚠ QC {failure}")
        print()

    if report.blocked:
        print("  BLOCKED")
        print("  " + "-" * 74)
        for s in report.blocked:
            print(f"  {s.template_id:<16}{s.dish[:19]:<20}{s.reason}")
        print()

    if report.gaps:
        print("  INGREDIENT WORKLIST — extract these from IFCT 2017, most blocking first")
        print("  " + "-" * 74)
        print(f"  {'ifct':<8}{'ingredient':<38}{'group':<12}{'blocks':>7}")
        for gap in report.gaps[:args.worklist]:
            print(f"  {gap.ifct_code or '—':<8}{gap.name[:37]:<38}"
                  f"{gap.group:<12}{gap.blocked_count:>7}")
        if len(report.gaps) > args.worklist:
            print(f"  ... and {len(report.gaps) - args.worklist} more")
        print()
        print(f"  {len(report.gaps)} ingredients block {len(report.blocked)} templates.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pathyam_engine.authoring")
    parser.add_argument("--dir", type=Path, default=None, help="db/templates directory")
    sub = parser.add_subparsers(dest="command", required=True)

    v = sub.add_parser("validate", help="validate the authored library")
    v.add_argument("--json", action="store_true")
    v.set_defaults(func=cmd_validate)

    l = sub.add_parser("load", help="load into PostgreSQL")
    l.add_argument("--dsn", default=os.environ.get("PATHYAM_DSN"))
    l.add_argument("--dry-run", action="store_true")
    l.add_argument("--lexicon", default=str(
        Path(__file__).resolve().parents[2] / "eval" / "lexicon_south_indian.yaml"),
        help="dish lexicon YAML; loaded into ref.food_name alongside the templates")
    l.set_defaults(func=cmd_load)

    a = sub.add_parser("audit", help="report computable templates and the ingredient worklist")
    a.add_argument("--dsn", default=os.environ.get("PATHYAM_DSN"))
    a.add_argument("--samples", type=int, default=800)
    a.add_argument("--worklist", type=int, default=30)
    a.add_argument("--json", action="store_true")
    a.set_defaults(func=cmd_audit)

    args = parser.parse_args(argv)
    if args.command in {"load", "audit"} and not args.dsn:
        parser.error("no DSN: pass --dsn or set PATHYAM_DSN")
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
