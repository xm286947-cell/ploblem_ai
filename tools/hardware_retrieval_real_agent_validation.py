"""Explicit, manual Codex harness for real Hardware Retrieval Agent validation.

Input is a JSON array of Formal-derived hardware-knowledge-consumption/v1
projections. This tool is intentionally not a pytest test or CI entry point.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, Sequence

from services.hardware_retrieval_tagger import HardwareRetrievalTagger
from services.hardware_retrieval_tagger_runtime import (
    build_hardware_retrieval_tagger_runtime,
)


def main(
    argv: Sequence[str] | None = None,
    *,
    runtime_factory: Callable[[], Any] = build_hardware_retrieval_tagger_runtime,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="JSON array of formal-derived projections")
    parser.add_argument(
        "--execute-real",
        action="store_true",
        help="explicitly authorize real Provider calls for this manual validation",
    )
    args = parser.parse_args(argv)

    # Enforce the affirmative CLI authorization before reading data, creating a
    # Runtime DB, resolving provider configuration, or constructing an invoker.
    if not args.execute_real:
        print("REAL_PROVIDER_EXECUTION_NOT_AUTHORIZED", file=sys.stderr)
        return 2

    try:
        projections = json.loads(args.input.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        print(f"INPUT_INVALID: {type(error).__name__}", file=sys.stderr)
        return 2
    if not isinstance(projections, list) or not projections:
        print("INPUT_MUST_BE_NONEMPTY_PROJECTION_ARRAY", file=sys.stderr)
        return 2
    if any(not isinstance(projection, dict) for projection in projections):
        print("INPUT_PROJECTIONS_MUST_BE_OBJECTS", file=sys.stderr)
        return 2

    try:
        tagger = HardwareRetrievalTagger(runtime_factory())
        results = [tagger.tag(projection) for projection in projections]
    except Exception as error:
        code = str(getattr(error, "code", "") or "VALIDATION_FAILED")
        print(json.dumps({"status": "FAILED", "error_code": code}, ensure_ascii=False))
        return 1

    print(json.dumps({"status": "COMPLETED", "results": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
