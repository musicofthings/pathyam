"""Inspect and check the OpenRouter vision catalogue.

    PYTHONPATH=. python3 -m pathyam_engine.vision                 # check PATHYAM_VISION_MODEL
    PYTHONPATH=. python3 -m pathyam_engine.vision --list          # usable models, free first
    PYTHONPATH=. python3 -m pathyam_engine.vision --list --all    # include unusable ones
    PYTHONPATH=. python3 -m pathyam_engine.vision --select free   # what "auto:free" would pick
    PYTHONPATH=. python3 -m pathyam_engine.vision <model-id>      # check one id

This exists because the failure that shipped was a model id nobody could check:
`gemini-3.7-flash` is not a real model, so every call failed, and the failure was
swallowed. The catalogue endpoint needs no API key, so this runs before an account
does.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

from .openrouter_provider import (
    FREE_TIER_WARNING,
    PREFER_FREE,
    VisionError,
    VisionModel,
    fetch_catalogue,
    resolve_model,
    select_vision_model,
    usable_models,
)


def _why_unusable(m: VisionModel, today: dt.date) -> str:
    if m.response_format_mode == "none":
        return "no JSON mode"
    if m.expires_on is not None and m.expires_on <= today + dt.timedelta(days=30):
        return f"expires {m.expires_on}"
    return ""


def _row(m: VisionModel, today: dt.date) -> str:
    cost = "free" if m.is_free else f"${m.cost_per_meal_usd():.4f}"
    expiry = f"  expires {m.expires_on}" if m.expires_on else ""
    reason = _why_unusable(m, today)
    flag = f"  [{reason}]" if reason else ""
    return (f"  {m.id:<58} {cost:>9}  {m.response_format_mode:<12} "
            f"{m.context_length:>8} ctx{expiry}{flag}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pathyam_engine.vision")
    parser.add_argument("model", nargs="?", help="model id (default: $PATHYAM_VISION_MODEL)")
    parser.add_argument("--list", action="store_true", help="list image-capable models")
    parser.add_argument("--all", action="store_true",
                        help="with --list, include models this provider cannot drive")
    parser.add_argument("--filter", default="", help="substring filter for --list")
    parser.add_argument("--select", metavar="PREFERENCE", nargs="?", const=PREFER_FREE,
                        help="show what auto-selection would pick (free|cheapest|any)")
    args = parser.parse_args(argv)

    today = dt.date.today()

    try:
        if args.list:
            catalogue = fetch_catalogue()
            shown = catalogue if args.all else usable_models(catalogue, today=today)
            shown = [m for m in shown if args.filter in m.id]
            # Free first, then by the same ranking auto-selection uses, so the top of
            # this list is what "auto:free" would choose.
            shown.sort(key=lambda m: (not m.is_free,
                                      0 if m.supports_structured_outputs else 1,
                                      -m.context_length, m.id))
            for m in shown:
                print(_row(m, today))

            free_usable = [m for m in usable_models(catalogue, today=today) if m.is_free]
            print(f"\n{len(shown)} shown · {len(catalogue)} image-capable · "
                  f"{len(free_usable)} free and usable", file=sys.stderr)
            if free_usable:
                print(f"\n  NOTE: {FREE_TIER_WARNING}", file=sys.stderr)
            return 0

        if args.select is not None:
            catalogue = fetch_catalogue()
            chosen = select_vision_model(catalogue, prefer=args.select, today=today)
            print(chosen.id)
            print(f"\n  {_row(chosen, today).strip()}", file=sys.stderr)
            if chosen.is_free:
                print(f"\n  NOTE: {FREE_TIER_WARNING}", file=sys.stderr)
            if chosen.response_format_mode == "json_object":
                print("\n  NOTE: this model cannot be held to a strict schema. The "
                      "schema is sent in the prompt and only our parser checks the "
                      "reply, so malformed extractions surface as errors, not as "
                      "silently wrong meals.", file=sys.stderr)
            return 0

        spec = args.model or os.environ.get("PATHYAM_VISION_MODEL", "").strip()
        if not spec:
            print(
                "PATHYAM_VISION_MODEL is not set and no model id was given.\n"
                'Set it to a model id, or to "auto" / "auto:free" / "auto:cheapest".\n'
                "Run with --list to see what is available.",
                file=sys.stderr,
            )
            return 2

        chosen, why = resolve_model(spec, today=today)
    except VisionError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    print(f"OK: {why}")
    print(_row(chosen, today))
    if chosen.is_free:
        print(f"\n  NOTE: {FREE_TIER_WARNING}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
