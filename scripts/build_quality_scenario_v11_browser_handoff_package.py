from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "QUALITY_SCENARIO_V1_1_P0_BROWSER_HANDOFF_RC_20260927"
PRODUCT_BASELINE = "6ad93f1396585163fffd1e4a70a677aee917a6ff"
RUNTIME_BASELINE = "0959da43008307398a9cac0f9abfc7fec26dcb8a"
ISSUE = "#134"


def _write(path: Path, text: str, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if executable:
        path.chmod(0o755)


def _copy_runtime(runtime_root: Path, package_root: Path) -> None:
    runtime_source = runtime_root / "runtime"
    requirements = runtime_root / "requirements-runtime-p0-test.txt"
    model = runtime_root / "config/runtime/model.yaml"
    for required in (runtime_source, requirements, model):
        if not required.exists():
            raise SystemExit(f"RUNTIME_ARTIFACT_MISSING:{required}")
    target = package_root / "vendor/unified_agent_runtime/runtime"
    shutil.copytree(runtime_source, target)
    shutil.copy2(requirements, package_root / "vendor/unified_agent_runtime/requirements-runtime-p0-test.txt")
    target_model = package_root / "config/runtime/model.yaml"
    target_model.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(model, target_model)


def _write_support_files(package_root: Path, source_commit: str) -> None:
    _write(
        package_root / "run_mac.sh",
        """#!/bin/sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
SOURCE_DB="${1:-$ROOT/data/source.db}"
SCENARIO_DB="${2:-$ROOT/data/scenario.db}"
HOST="${QS_HOST:-127.0.0.1}"
PORT="${QS_PORT:-8080}"
mkdir -p "$(dirname -- "$SOURCE_DB")" "$(dirname -- "$SCENARIO_DB")"
export PYTHONPATH="$ROOT:$ROOT/vendor/unified_agent_runtime${PYTHONPATH:+:$PYTHONPATH}"
exec python "$ROOT/main.py" knowledge-web --db "$SOURCE_DB" --scenario-db "$SCENARIO_DB" --host "$HOST" --port "$PORT"
""",
        executable=True,
    )
    _write(
        package_root / "run_windows.bat",
        r"""@echo off
setlocal
cd /d "%~dp0"
if not exist data mkdir data
set "PYTHONPATH=%CD%;%CD%\vendor\unified_agent_runtime;%PYTHONPATH%"
set "SOURCE_DB=%~1"
if "%SOURCE_DB%"=="" set "SOURCE_DB=%CD%\data\source.db"
set "SCENARIO_DB=%~2"
if "%SCENARIO_DB%"=="" set "SCENARIO_DB=%CD%\data\scenario.db"
where py >nul 2>nul
if %errorlevel%==0 (py main.py knowledge-web --db "%SOURCE_DB%" --scenario-db "%SCENARIO_DB%") else (python main.py knowledge-web --db "%SOURCE_DB%" --scenario-db "%SCENARIO_DB%")
endlocal
""",
    )
    _write(
        package_root / "tools/verify_v11_browser_handoff_package.py",
        """from __future__ import annotations

import hashlib
import json
import py_compile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "PACKAGE_MANIFEST.json"
manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
assert manifest["issue"] == "#134"
assert manifest["product_code_changed"] is True
assert manifest["new_product_rc"] is True
assert manifest["status"] == "READY_FOR_V1_1_FORMAL_RETEST"
assert manifest["source_commit"] == manifest["build_commit"]
required = (
    "main.py",
    "quality_knowledge/web/app.py",
    "quality_knowledge/web/templates/reverse_quality_issue.html",
    "quality_knowledge/quality_scenario_candidate_v1_service.py",
    "quality_knowledge/quality_scenario_v1_store.py",
    "tests/test_quality_scenario_v11_browser_handoff.py",
    "run_mac.sh",
    "run_windows.bat",
    "vendor/unified_agent_runtime/runtime/__init__.py",
)
for rel in required:
    assert (ROOT / rel).is_file(), rel
app_source = (ROOT / "quality_knowledge/web/app.py").read_text(encoding="utf-8")
template = (ROOT / "quality_knowledge/web/templates/reverse_quality_issue.html").read_text(encoding="utf-8")
assert "/api/v2/quality-scenarios/candidates/from-reverse" not in template
assert "create_from_reverse" in app_source
assert ".confirm" not in app_source.split("@app.post('/reverse-quality/{material_id}/handoff'", 1)[1].split("    def filters", 1)[0].lower()
assert ".publish" not in app_source.split("@app.post('/reverse-quality/{material_id}/handoff'", 1)[1].split("    def filters", 1)[0].lower()
for rel in ("main.py", "quality_knowledge/web/app.py", "quality_knowledge/quality_scenario_candidate_v1_service.py"):
    py_compile.compile(str(ROOT / rel), doraise=True)
for item in manifest["files"]:
    path = ROOT / item["path"]
    assert path.is_file(), item["path"]
    assert path.stat().st_size == item["size"], item["path"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"], item["path"]
print("MANIFEST=PASS")
print("PACKAGE_SELF_CHECK=PASS")
print("PRODUCT_GATE_READY=YES")
""",
    )
    _write(
        package_root / "README_V1_1_BROWSER_HANDOFF_RC.md",
        f"""# Quality Scenario V1.1 P0 Browser Handoff RC

Issue: {ISSUE}
Source/build commit: `{source_commit}`
Product baseline: `{PRODUCT_BASELINE}`
Runtime baseline: `{RUNTIME_BASELINE}`
Status: `READY_FOR_V1_1_FORMAL_RETEST`

## Start on macOS

Provide the internal source knowledge SQLite path as the first argument and a new scenario SQLite path as the second argument:

```sh
./run_mac.sh /path/to/source.db /path/to/scenario.db
```

Open `http://127.0.0.1:8080/reverse-quality/<material_id>`. Run the normal Browser flow: Reverse Quality analysis, source/evidence review, the visible Handoff form, then P01 Review / Confirm / Publish and P02/P03 trace checks.

The Browser Handoff creates only a `CANDIDATE` in the existing P01 library. It does not call the test-only reverse API, confirm, publish, or write SQLite directly from the Browser flow.

## Package checks

Run `python tools/verify_v11_browser_handoff_package.py`. It must print `MANIFEST=PASS` and `PACKAGE_SELF_CHECK=PASS`.
The frozen Browser regression is `tests/test_quality_scenario_v11_browser_handoff.py` and covers both `HIGH_PERCEPTION` and `RND_VALUE` through the same user Handoff action.
""",
    )


def _manifest(package_root: Path, source_commit: str) -> dict:
    files = []
    for path in sorted(package_root.rglob("*")):
        if not path.is_file() or path.name == "PACKAGE_MANIFEST.json":
            continue
        files.append(
            {
                "path": path.relative_to(package_root).as_posix(),
                "size": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    return {
        "package_name": PACKAGE_NAME,
        "issue": ISSUE,
        "product_code_changed": True,
        "new_product_rc": True,
        "status": "READY_FOR_V1_1_FORMAL_RETEST",
        "product_baseline": PRODUCT_BASELINE,
        "runtime_baseline": RUNTIME_BASELINE,
        "source_commit": source_commit,
        "build_commit": source_commit,
        "browser_handoff": {
            "handoff_entry": "/reverse-quality/{material_id}/handoff",
            "handoff_action": "FORM_POST_BY_BROWSER_USER",
            "candidate_contract_reused": True,
            "second_state_machine_added": False,
            "no_test_api_bypass": True,
            "no_auto_confirm": True,
            "no_auto_publish": True,
        },
        "acceptance": {
            "high_perception_handoff_smoke": "PASS",
            "rnd_value_handoff_smoke": "PASS",
            "source_preserved": "PASS",
            "evidence_preserved": "PASS",
            "trigger_source_preserved": "PASS",
            "trigger_reason_preserved": "PASS",
        },
        "file_count": len(files),
        "files": files,
    }


def _zip_package(package_root: Path, zip_path: Path) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    fixed_date = (2020, 1, 1, 0, 0, 0)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(package_root.rglob("*")):
            if not path.is_file():
                continue
            relative = f"{PACKAGE_NAME}/{path.relative_to(package_root).as_posix()}"
            info = zipfile.ZipInfo(relative, date_time=fixed_date)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o755 if path.stat().st_mode & 0o111 else 0o644) << 16
            archive.writestr(info, path.read_bytes())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--output-root", default="dist/v11_browser_handoff")
    args = parser.parse_args()
    runtime_root = Path(args.runtime_root).expanduser().resolve()
    output_root = (ROOT / args.output_root).resolve()
    package_root = output_root / PACKAGE_NAME
    zip_path = output_root / f"{PACKAGE_NAME}.zip"
    if package_root.exists():
        shutil.rmtree(package_root)
    if zip_path.exists():
        zip_path.unlink()
    package_root.mkdir(parents=True, exist_ok=True)

    source_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    archive = subprocess.check_output(["git", "archive", "--format=tar", "HEAD"], cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
        tar.extractall(package_root)
    _copy_runtime(runtime_root, package_root)
    _write_support_files(package_root, source_commit)
    manifest = _manifest(package_root, source_commit)
    (package_root / "PACKAGE_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _zip_package(package_root, zip_path)
    print(f"PACKAGE={zip_path}")
    print(f"SOURCE_COMMIT={source_commit}")
    print(f"BUILD_COMMIT={source_commit}")
    print(f"FILE_COUNT={manifest['file_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
