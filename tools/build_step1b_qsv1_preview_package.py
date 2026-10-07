from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".pyc", ".pyo"}


def tracked_files() -> list[Path]:
    raw = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
    items = []
    for entry in raw.decode("utf-8").split("\0"):
        if not entry:
            continue
        path = Path(entry)
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            continue
        if "__pycache__" in path.parts:
            continue
        items.append(path)
    return items


def write_file(zf: zipfile.ZipFile, path: Path) -> None:
    source = ROOT / path
    info = zipfile.ZipInfo.from_file(source, arcname=path.as_posix())
    if path.as_posix() == "start_quality_capability_p1.command":
        info.external_attr = (stat.S_IFREG | 0o755) << 16
    with source.open("rb") as handle:
        zf.writestr(info, handle.read(), compress_type=zipfile.ZIP_DEFLATED)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="dist")
    args = parser.parse_args()

    source_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    short = source_sha[:12]
    output_dir = (ROOT / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    package = output_dir / f"STEP1B_QSV1_MATURE_PREVIEW_{short}.zip"

    manifest = {
        "contract": "step1b-qsv1-mature-preview/v1",
        "source_sha": source_sha,
        "candidate_class": "USER_PREVIEW",
        "formal_release_candidate": False,
        "mature_code_restore": "PASS",
        "mature_real_data_product_gate": "PENDING_EXTERNAL_DB",
        "qsv1_additive_integration": "PASS",
        "windows_entry": "start_quality_capability_p1.bat",
        "macos_entry": "start_quality_capability_p1.command",
        "qsv1_db_env": "QUALITY_SCENARIO_V1_DB_PATH",
        "synthetic_fixture_used": False,
        "routes": [
            "/p0/quality-scenarios/workbench",
            "/p0/quality-scenarios",
            "/p0/quality-scenario-insights",
        ],
        "data_truth_endpoint": "/api/v2/quality-scenario-preview/status",
        "legacy_routes_untouched": True,
    }
    readme = f"""STEP1B NEW QUALITYSCENARIO V1 IN MATURE PLATFORM PREVIEW

SOURCE_SHA={source_sha}
CANDIDATE_CLASS=USER_PREVIEW
PRODUCT_COMPLETION=NO
MATURE_CODE_RESTORE=PASS
MATURE_REAL_DATA_PRODUCT_GATE=PENDING_EXTERNAL_DB
QSV1_ADDITIVE_INTEGRATION=PASS
SYNTHETIC_FIXTURE_USED=NO

WINDOWS:
1. Run INSTALL_OVERALL_R2_WINDOWS.bat once if needed.
2. Optional real V1 DB binding:
   set QUALITY_SCENARIO_V1_DB_PATH=C:\\path\\to\\existing-qsv1.db
3. Run start_quality_capability_p1.bat

macOS:
1. Optional real V1 DB binding:
   export QUALITY_SCENARIO_V1_DB_PATH=/path/to/existing-qsv1.db
2. Run ./start_quality_capability_p1.command
   Python 3.12 is supported.

PREVIEW:
- Mature base: /issues /analysis /import
- New workbench: /p0/quality-scenarios/workbench
- New library: /p0/quality-scenarios
- Detail/source/evidence/history: enter from the V1 library
- Product/customer/industry insight: /p0/quality-scenario-insights
- Data truth: /api/v2/quality-scenario-preview/status

DATA POLICY:
- No SQLite production/internal data is packaged.
- No fixture/synthetic scenario is created.
- If no real V1 data is bound, counts are 0 and P04 is EMPTY.
- Old /quality-scenarios namespace is not overwritten by STEP1B.
"""

    with zipfile.ZipFile(package, "w") as zf:
        for path in tracked_files():
            write_file(zf, path)
        zf.writestr(
            "00_STEP1B_README_FIRST.txt",
            readme.encode("utf-8"),
            compress_type=zipfile.ZIP_DEFLATED,
        )
        zf.writestr(
            "STEP1B_PREVIEW_MANIFEST.json",
            json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
            compress_type=zipfile.ZIP_DEFLATED,
        )
        zf.writestr("STEP1B_SOURCE_COMMIT", (source_sha + "\n").encode("ascii"))

    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    print(f"SOURCE_SHA={source_sha}")
    print(f"PACKAGE={package.name}")
    print(f"PACKAGE_PATH={package}")
    print(f"SHA256={digest}")
    print("USER_PREVIEW_PACKAGE=READY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
