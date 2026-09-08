"""Check the configured vision model against the live OpenRouter catalogue.

    PYTHONPATH=. python3 -m pathyam_engine.vision            # check PATHYAM_VISION_MODEL
    PYTHONPATH=. python3 -m pathyam_engine.vision --list     # what accepts images
    PYTHONPATH=. python3 -m pathyam_engine.vision <model-id> # check one id

This exists because the failure that shipped was a model id nobody could check:
`gemini-3.7-flash` is not a real model, so every call failed, and the failure was
swallowed. The catalogue endpoint needs no API key, so this runs before an account
does.
"""

from __future__ import annotations

import argparse
import os
import sys

from .openrouter_provider import VisionError, list_vision_models, verify_model_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pathyam_engine.vision")
    parser.add_argument("model", nargs="?", help="model id (default: $PATHYAM_VISION_MODEL)")
    parser.add_argument("--list", action="store_true", help="list image-capable models")
    parser.add_argument("--filter", default="", help="substring filter for --list")
    args = parser.parse_args(argv)

    try:
        if args.list:
            models = list_vision_models()
            shown = [m for m in models if args.filter in m]
            for model_id in shown:
                print(model_id)
            print(f"\n{len(shown)} of {len(models)} image-capable models", file=sys.stderr)
            return 0

        model_id = args.model or os.environ.get("PATHYAM_VISION_MODEL", "").strip()
        if not model_id:
            print(
                "PATHYAM_VISION_MODEL is not set and no model id was given.\n"
                "Run with --list to see what accepts images.",
                file=sys.stderr,
            )
            return 2

        verify_model_id(model_id)
    except VisionError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    print(f"OK: {model_id} is in the catalogue and accepts images")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
