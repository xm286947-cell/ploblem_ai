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
    if path.as_posix() in {"start_quality_capability_p1.command", "START_OVERALL_CURRENT_PLATFORM_MAC.command"}:
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
    package = output_dir / f"QUALITY_SCENARIO_MATURE_INTEGRATION_CANDIDATE_{short}.zip"

    manifest = {
        "contract": "quality-scenario-mature-integration-candidate/v1",
        "source_sha": source_sha,
        "candidate_class": "CODEX_VALIDATION_CANDIDATE",
        "formal_release_candidate": False,
        "mature_runtime_root": "quality_knowledge.web.app.create_app",
        "default_entry": "/issues",
        "windows_entry": "START_OVERALL_CURRENT_PLATFORM_WINDOWS.bat",
        "macos_entry": "START_OVERALL_CURRENT_PLATFORM_MAC.command",
        "qsv1_db_env": "QUALITY_SCENARIO_V1_DB_PATH",
        "synthetic_fixture_used": False,
        "production_entry": "/software-assessment#quality-scenario-production",
        "product_routes": [
            "/issues",
            "/software-assessment",
            "/quality-scenarios/workbench",
            "/quality-scenarios/library",
            "/quality-scenarios/library/{scenario_id}",
        ],
        "compatibility_only_routes": [
            "/p0/quality-scenarios/workbench",
            "/p0/quality-scenarios",
            "/p0/quality-scenario-insights",
        ],
        "data_truth_endpoint": "/api/v2/quality-scenario-preview/status",
        "p0_product_entry": False,
    }
    readme = f"""QUALITY SCENARIO MATURE INTEGRATION - CODEX VALIDATION CANDIDATE

SOURCE_SHA={source_sha}
CANDIDATE_CLASS=CODEX_VALIDATION_CANDIDATE
FORMAL_RELEASE_CANDIDATE=NO
MATURE_RUNTIME_ROOT=quality_knowledge.web.app.create_app
DEFAULT_ENTRY=/issues
P0_PRODUCT_ENTRY=NO
SYNTHETIC_FIXTURE_USED=NO

START:
Windows:
  START_OVERALL_CURRENT_PLATFORM_WINDOWS.bat
macOS:
  ./START_OVERALL_CURRENT_PLATFORM_MAC.command

PRODUCT FLOW:
  /software-assessment#quality-scenario-production
    -> /quality-scenarios/workbench
    -> Review / Confirm / Publish
    -> /quality-scenarios/library
    -> /quality-scenarios/library/{{scenario_id}}
    -> portrait / insights

CODEX VALIDATION:
1. Fresh Extract only. Do not run from the source checkout.
2. Verify SHA256 supplied alongside this package.
3. Confirm STEP1B_SOURCE_COMMIT equals SOURCE_SHA above.
4. Do not modify code before completing the first validation pass.
5. Run exact Windows or macOS product launcher.
6. Confirm / redirects to /issues and mature routes are reachable.
7. Confirm mature navigation does not expose /p0/quality-scenarios/workbench as product entry.
8. Confirm /software-assessment contains the Quality Scenario production controls.
9. Confirm /quality-scenarios/workbench, /quality-scenarios/library and detail route load through the mature host.
10. Run focused regression:
   python -m pytest -q tests/test_r2_w2_qsv1_production_chain.py tests/test_step1b_qsv1_mature_preview.py tests/test_software_assessment_qsv1_browser_flow.py tests/test_quality_scenario_w4_functional_fixture.py tests/test_quality_scenario_w4_functional_provider_e2e.py tests/test_quality_scenario_mature_integration.py
11. If browser execution is available, execute G1-G5 from the mature /software-assessment UI. Do not create candidates by direct database writes.
12. Report PASS/FAIL/BLOCKED with exact evidence and do not silently repair failures.

EXPECTED:
- Mature host is the product runtime root.
- P0 remains compatibility code only, not the product entry.
- W1-W4 business chain is reused, not rebuilt.
- G2 missing missed-test remains MISSING.
- G3 conflict fails closed.
- G4 duplicate generation returns EXISTING_CANDIDATE.
- G5 source revision creates new lineage while preserving prior history.

DATA POLICY:
- No production/internal SQLite data is packaged.
- No real credentials are packaged.
- Do not print or upload secrets.
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
        zf.writestr(
            "CODEX_VALIDATION_TASK.md",
            readme.encode("utf-8"),
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
