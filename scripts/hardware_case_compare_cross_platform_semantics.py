from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("dist/cross-platform-semantics")
    files = sorted(root.rglob("cross-platform-semantics-*.json"))
    if len(files) != 2:
        raise SystemExit(f"SEMANTICS_FILES_REQUIRED=2,FOUND={len(files)}")
    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in files]
    if payloads[0] != payloads[1]:
        raise SystemExit(
            "CROSS_PLATFORM_SEMANTICS_MISMATCH="
            + json.dumps(
                {"files": [str(path) for path in files], "left": payloads[0], "right": payloads[1]},
                ensure_ascii=False,
            )
        )
    print("CROSS_PLATFORM_SEMANTICS=PASS")
    print("SOURCE_COMMIT=" + str(payloads[0]["source_commit"]))
    print("PUBLIC_CONTRACT_VERSION=" + str(payloads[0]["public_contract_version"]))
    print("SCHEMA_VERSION=" + str(payloads[0]["schema_version"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
