from __future__ import annotations

import hashlib
import json
import os
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
PACKAGE_NAME = "HARDWARE_CASE_PRODUCT_TEST_V0.1.1"
STAGE = DIST / PACKAGE_NAME

INCLUDE_DIRS = [
    "builder",
    "common",
    "compatibility",
    "contracts",
    "models",
    "quality_knowledge",
    "repositories",
    "retriever",
    "runtime",
    "services",
    "schema",
]

INCLUDE_FILES = [
    "00_README_FIRST.txt",
    "main.py",
    "requirements.txt",
    "requirements-runtime-p0-test.txt",
    "config/runtime/model.local.hardware_case.example.yaml",
    "config/runtime/agents/hardware_case.structure.yaml",
    "config/hardware_case_real_validation.local.example.json",
    "prompts/runtime/hardware_case/structure_v1.md",
    "tools/hardware_case_real_validation.py",
    "scripts/hardware_case_mvp_smoke.py",
    "scripts/hardware_case_product_test_smoke.py",
    "scripts/hardware_case_precheck.py",
    "INIT_LOCAL_CONFIG.bat",
    "CHECK_ENV.bat",
    "START_HARDWARE_CASE.bat",
    "RUN_REAL_AI_VALIDATION.bat",
    "INIT_LOCAL_CONFIG.sh",
    "START_HARDWARE_CASE.sh",
    "RUN_REAL_AI_VALIDATION.sh",
    "run_hardware_case_product_test.bat",
    "run_hardware_case_product_test.sh",
    "run_hardware_case_mvp_smoke.bat",
    "run_hardware_case_mvp_smoke.sh",
    "docs/product/HARDWARE_CASE_PRODUCT_TEST_V0.1.1.md",
]

EXCLUDED_NAMES = {
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".DS_Store", ".git", "dist",
}
EXCLUDED_SUFFIXES = {
    ".pyc", ".pyo", ".db", ".sqlite", ".sqlite3", ".log", ".zip",
}
SENSITIVE_EXACT_NAMES = {
    ".env", ".env.local", "model.local.yaml", "model.local.yml",
    "agent.local.yaml", "agent.local.yml",
}
SENSITIVE_PATTERNS = (
    ".local.yaml", ".local.yml", ".secret.yaml", ".secret.yml",
)


def allowed(path: Path) -> bool:
    if any(part in EXCLUDED_NAMES for part in path.parts):
        return False
    if path.suffix.lower() in EXCLUDED_SUFFIXES:
        return False
    lower = path.name.lower()
    if lower in SENSITIVE_EXACT_NAMES:
        return False
    if any(lower.endswith(pattern) for pattern in SENSITIVE_PATTERNS):
        return False
    return True


def copy_tree(src: Path, dst: Path) -> None:
    for item in src.rglob("*"):
        if not item.is_file() or not allowed(item):
            continue
        relative = item.relative_to(src)
        target = dst / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inventory() -> list[dict[str, object]]:
    files: list[dict[str, object]] = []
    for item in sorted(STAGE.rglob("*")):
        if item.is_file():
            files.append({
                "path": item.relative_to(STAGE).as_posix(),
                "sha256": sha256(item),
                "size": item.stat().st_size,
            })
    return files


def security_assertions(files: list[dict[str, object]]) -> None:
    forbidden: list[str] = []
    for entry in files:
        path = Path(str(entry["path"]))
        if not allowed(path):
            forbidden.append(path.as_posix())
        lower = path.name.lower()
        if lower in SENSITIVE_EXACT_NAMES or any(lower.endswith(p) for p in SENSITIVE_PATTERNS):
            forbidden.append(path.as_posix())
    if forbidden:
        raise SystemExit("FORBIDDEN_PACKAGE_FILES=" + ",".join(sorted(set(forbidden))))


def main() -> int:
    shutil.rmtree(STAGE, ignore_errors=True)
    DIST.mkdir(parents=True, exist_ok=True)
    STAGE.mkdir(parents=True, exist_ok=True)

    for relative in INCLUDE_DIRS:
        source = ROOT / relative
        if not source.is_dir():
            raise SystemExit(f"MISSING_REQUIRED_DIR={relative}")
        copy_tree(source, STAGE / relative)

    for relative in INCLUDE_FILES:
        source = ROOT / relative
        if not source.is_file():
            raise SystemExit(f"MISSING_REQUIRED_FILE={relative}")
        target = STAGE / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    # Empty company-local working folders are intentionally created in the
    # package. Real data is supplied only after extraction inside the company.
    for relative in (
        "data/input/word",
        "data/tree",
        "data/output",
        "data/runtime",
    ):
        (STAGE / relative).mkdir(parents=True, exist_ok=True)

    files = inventory()
    security_assertions(files)
    source_commit = os.getenv("GITHUB_SHA", "LOCAL")

    manifest = {
        "package": PACKAGE_NAME,
        "product": "HARDWARE_CASE",
        "target_version": "MVP_V0.1",
        "package_revision": "V0.1.1_RUNTIME_READY",
        "package_status": "READY_FOR_INTERNAL_TEST",
        "release_status": "TEST_PACKAGE_NOT_RELEASE",
        "source_commit": source_commit,
        "contract_version": "hardware-case/v1",
        "tree_import_contract_version": "hardware-tree-import/v1",
        "web_entry": "/p0/hardware-cases/base-data",
        "api_prefix": "/api/v2/hardware-cases",
        "entrypoints": {
            "init_local_config_windows": "INIT_LOCAL_CONFIG.bat",
            "precheck_windows": "CHECK_ENV.bat",
            "start_product_windows": "START_HARDWARE_CASE.bat",
            "real_ai_validation_windows": "RUN_REAL_AI_VALIDATION.bat",
            "start_product_shell": "START_HARDWARE_CASE.sh",
            "real_ai_validation_shell": "RUN_REAL_AI_VALIDATION.sh",
        },
        "runtime": {
            "agent_id": "hardware_case.structure",
            "agent_config": "config/runtime/agents/hardware_case.structure.yaml",
            "model_template": "config/runtime/model.local.hardware_case.example.yaml",
            "local_model_config": "config/runtime/model.local.yaml",
            "runtime_adapter": "services.hardware_case_runtime_adapter:build_hardware_case_structurer",
            "provider_ownership": "UNIFIED_RUNTIME",
            "secret_policy": "ENV_REFERENCE_RECOMMENDED",
        },
        "test_scope": [
            "P07 base-data management frontend",
            "Circuit/Feature and Material/Device tree import workflow",
            "Excel upload / Sheet+Header / dynamic Mapping / Preview / Validation",
            "Change Diff / Conflict / EXCLUDE / Atomic Apply / Tree Version / History",
            "Hardware Case backend/API publish and consume Golden Path",
            "DOCX parser + AI Adapter boundary",
            "Hardware Case Agent Config + Unified Runtime Adapter",
            "Local model configuration template and environment precheck",
            "One-click Web start + company-local Real AI validation entry",
            "Company-only Real Validation Harness",
            "Synthetic package smoke",
        ],
        "frozen_tree_change_types": [
            "ADD", "UPDATE", "RENAME", "MOVE", "DEPRECATE", "NO_CHANGE", "CONFLICT",
        ],
        "explicitly_not_tree_change_type": ["EXCLUDE", "DELETE"],
        "open_test_gates": [
            "REAL_TREE_IMPORT_VALIDATION",
            "M4_REAL_DATA_VALIDATED",
            "AI_INTEGRATION_GATE_PASS",
            "20_TO_30_REAL_CASE_MVP_INTEGRATION_GATE_PASS",
            "HC_TREE_IMPORT_PRODUCT_GATE",
        ],
        "known_gaps": [
            "P01-P06 Hardware Case dedicated formal frontend is not part of this package",
            "Real company Word/Excel data is not bundled",
            "Real Provider acceptance has not yet been passed; the package now contains the runnable Runtime/Agent entry for company validation",
        ],
        "explicitly_not_claimed": [
            "MVP_INTEGRATION_GATE_PASS",
            "MVP_DEMO_GATE_PASS",
            "RELEASE_GATE_PASS",
            "Pilot Ready",
        ],
        "security_exclusions": [
            "real company Word/Excel/image materials",
            "runtime databases and business data",
            "API keys and Authorization values",
            ".env and real local/secret YAML configuration; only placeholder examples are packaged",
            "provider raw content and prompts from real runs",
        ],
        "files": files,
    }

    manifest_path = STAGE / "PACKAGE_MANIFEST.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    short = source_commit[:12] if source_commit != "LOCAL" else "LOCAL"
    archive = DIST / f"{PACKAGE_NAME}_{short}.zip"
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for item in sorted(STAGE.rglob("*")):
            if item.is_file():
                bundle.write(item, item.relative_to(DIST).as_posix())

    checksum = sha256(archive)
    checksum_path = archive.with_suffix(".zip.sha256")
    checksum_path.write_text(f"{checksum}  {archive.name}\n", encoding="utf-8")

    print(f"PACKAGE={archive}")
    print(f"SHA256={checksum}")
    print(f"SIZE={archive.stat().st_size}")
    print(f"FILES={len(files) + 1}")
    print("PACKAGE_STATUS=READY_FOR_INTERNAL_TEST")
    print("RELEASE_STATUS=TEST_PACKAGE_NOT_RELEASE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
