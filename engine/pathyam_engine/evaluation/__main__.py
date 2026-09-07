"""CLI for the resolution evaluation harness."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..resolution import DishResolver
from .harness import (ablate, build_source, evaluate, evaluate_leave_one_out,
                      load_lexicon, load_queries)

_DEFAULT_DIR = Path(__file__).resolve().parents[2] / "eval"


def _pct(value: float | None) -> str:
    return "   -  " if value is None else f"{value * 100:5.1f}%"


def _render(report, failures: int) -> str:
    lines: list[str] = []
    add = lines.append

    add("=" * 78)
    add(f"  RESOLUTION EVALUATION — {report.label}")
    add("=" * 78)
    add(f"  {len(report.outcomes)} queries · {report.lexicon_size} lexicon entries "
        f"· median {report.median_latency_ms:.2f} ms/query")
    add("")
    add(f"  top-1  {_pct(report.top1)}      top-5  {_pct(report.top5)}"
        f"      MRR  {report.mrr:.3f}")
    add("")

    add("  BY CATEGORY")
    add("  " + "-" * 74)
    add(f"  {'category':<22}{'n':>5}{'top-1':>10}{'top-5':>10}{'correct':>10}")
    for name, stats in report.by_category().items():
        add(f"  {name:<22}{stats['n']:>5}{_pct(stats['top1']):>10}"
            f"{_pct(stats['top5']):>10}{_pct(stats['correct']):>10}")
    add("")

    a = report.abstention()
    add("  ABSTENTION CALIBRATION")
    add("  " + "-" * 74)
    add(f"  asked for confirmation      {a['asked']} / {len(report.outcomes)} "
        f"({_pct(a['asked_pct']).strip()})")
    add(f"  ...of those, would be wrong {_pct(a['useful']).strip()}   "
        f"(high = asked when it mattered)")
    add(f"  errors it did NOT flag      {_pct(a['silent_errors']).strip()}   "
        f"(low = few confident mistakes)")
    add(f"  recall on cases marked      {_pct(a['expected_ask_recall']).strip()}   "
        f"'should ask' in the golden set")
    noise = report.absent_noise()
    if noise is not None:
        add(f"  absent dishes offered a     {_pct(noise).strip()}   "
            f"candidate anyway (pointless prompts)")
    add("")

    bad = report.failures(failures)
    if bad:
        add(f"  FAILURES ({len(report.failures(10_000))} total, showing {len(bad)})")
        add("  " + "-" * 74)
        add(f"  {'query':<26}{'expected':<20}{'predicted':<20}{'rank':>5}")
        for o in bad:
            expected = o.query.expect or "(none)"
            predicted = o.predicted or "(none)"
            rank = str(o.rank) if o.rank else "-"
            add(f"  {o.query.q[:25]:<26}{expected[:19]:<20}{predicted[:19]:<20}{rank:>5}")
        add("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pathyam_engine.evaluation")
    parser.add_argument("--lexicon", type=Path,
                        default=_DEFAULT_DIR / "lexicon_south_indian.yaml")
    parser.add_argument("--queries", type=Path,
                        default=_DEFAULT_DIR / "golden_queries.yaml")
    parser.add_argument("--failures", type=int, default=25)
    parser.add_argument("--no-ablation", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    dishes = load_lexicon(args.lexicon)
    queries = load_queries(args.queries)

    # Three modes:
    #   catalogued     — every tested spelling is in the alias table. What production
    #                    looks like once the lexicon is built out.
    #   leave-one-out  — only the query's OWN alias is removed, so the dish keeps its
    #                    other spellings. This is the number to tune ranking against:
    #                    it models a user typing an unseen romanisation of a dish the
    #                    lexicon already covers.
    #   global holdout — every golden query string is removed at once. Deliberately
    #                    harsher, and for a well-covered dish unrealistically so:
    #                    six of dosa_plain's nine surfaces are golden queries, so it
    #                    is left with no bare name while siblings keep theirs. Kept
    #                    as a floor, and it is the mode the ablation runs in.
    full_source = build_source(dishes)
    held_source = build_source(dishes, holdout=[q.q for q in queries])

    catalogued = evaluate(DishResolver(full_source), queries,
                          label="catalogued (aliases present)",
                          lexicon_size=len(full_source.entries))
    report = evaluate_leave_one_out(dishes, queries,
                                    label="leave-one-out (unseen spelling, dish known)")
    global_holdout = evaluate(DishResolver(held_source), queries,
                              label="global holdout (all spellings removed)",
                              lexicon_size=len(held_source.entries))

    if args.json:
        payload = {"catalogued": catalogued.as_dict(),
                   "leave_one_out": report.as_dict(),
                   "global_holdout": global_holdout.as_dict()}
        if not args.no_ablation:
            payload["ablations"] = [r.as_dict() for r in ablate(dishes, queries)]
        json.dump(payload, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0

    print(f"  catalogued lexicon: top-1 {_pct(catalogued.top1).strip()}   "
          f"({len(full_source.entries)} entries)")
    print(f"  global holdout:     top-1 {_pct(global_holdout.top1).strip()}   "
          f"({len(held_source.entries)} entries, all golden spellings removed)")
    print(f"  leave-one-out:      top-1 {_pct(report.top1).strip()}   "
          f"({report.lexicon_size} entries, this query's own spelling removed)")
    print()
    print(_render(report, args.failures))

    if not args.no_ablation:
        print("=" * 78)
        print("  ABLATION — what each retrieval component contributes")
        print("=" * 78)
        print(f"  {'configuration':<32}{'top-1':>10}{'top-5':>10}{'MRR':>10}{'delta':>10}")
        reports = ablate(dishes, queries)
        baseline_top1 = reports[0].top1
        for r in reports:
            delta = r.top1 - baseline_top1
            marker = "" if r.label == "baseline" else f"{delta * 100:+6.1f} pts"
            print(f"  {r.label:<32}{_pct(r.top1):>10}{_pct(r.top5):>10}"
                  f"{r.mrr:>10.3f}{marker:>10}")
        print()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
