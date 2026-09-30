from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.hardware_case_word import HardwareWordParseError, parse_docx


def main() -> int:
    parser = argparse.ArgumentParser(description="Hardware R1 DOCX snapshot parser")
    parser.add_argument("docx")
    parser.add_argument("--output")
    args = parser.parse_args()

    source = Path(args.docx)
    try:
        snapshot = parse_docx(source).to_snapshot()
    except HardwareWordParseError as error:
        print(json.dumps({"status": "FAILED", "error_code": error.code}, ensure_ascii=False))
        return 2

    payload = json.dumps(snapshot, ensure_ascii=False, indent=2)
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload, encoding="utf-8")
        print(json.dumps({
            "status": "PASS",
            "source_id": snapshot["source"]["source_id"],
            "output": str(target),
        }, ensure_ascii=False))
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
