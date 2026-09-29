from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
PACKAGE_NAME = "HARDWARE_CASE_PRODUCT_TEST_FULL_V0.1"
PACKAGE_ARCHIVE_VARIANT = "DEPENDENCY_CLOSURE_R1"
STAGE = DIST / PACKAGE_NAME

INCLUDE_DIRS = [
    # Unified Runtime is the only executable shared platform subtree required
    # by Hardware Case. Business-domain trees are never copied wholesale.
    "runtime",
]

INCLUDE_GLOBS = [
    "services/hardware_case*.py",
    "services/hardware_tree*.py",
    "services/hardware_migrations/*.py",
    "repositories/hardware_case*.py",
    "repositories/hardware_tree*.py",
    "schema/hardware_case*.json",
    "schema/hardware_tree*.json",
    "schema/hardware_public*.json",
    "quality_knowledge/web/templates/hardware_case*.html",
    "quality_knowledge/web/static/hardware_case*.css",
    "quality_knowledge/web/static/hardware_case*.js",
]

INCLUDE_FILES = [
    "00_README_FIRST.txt",
    "requirements.txt",
    "requirements-runtime-p0-test.txt",
    "quality_knowledge/__init__.py",
    "quality_knowledge/web/__init__.py",
    "quality_knowledge/web/p0_app.py",
    "quality_knowledge/web/p0_pages.py",
    "quality_knowledge/web/hardware_case_api.py",
    "quality_knowledge/web/hardware_public_api.py",
    "quality_knowledge/web/hardware_operability_api.py",
    "quality_knowledge/web/hardware_tree_import_api.py",
    "quality_knowledge/web/templates/p0_base.html",
    "quality_knowledge/web/templates/_hardware_case_nav.html",
    "quality_knowledge/web/templates/hardware_tree_import.html",
    "quality_knowledge/web/static/app.css",
    "quality_knowledge/web/static/p0_ued_bridge.css",
    "quality_knowledge/web/static/hardware_tree_import.css",
    "quality_knowledge/web/static/hardware_tree_import.js",
    "repositories/__init__.py",
    "services/__init__.py",
    "services/hardware_operability.py",
    "config/runtime/model.local.hardware_case.example.yaml",
    "config/runtime/agents/hardware_case.structure.yaml",
    "config/hardware_case_real_validation.local.example.json",
    "prompts/runtime/hardware_case/structure_v1.md",
    "tools/hardware_case_real_validation.py",
    "scripts/hardware_case_mvp_smoke.py",
    "scripts/hardware_case_product_test_smoke.py",
    "scripts/hardware_case_precheck.py",
    "scripts/hardware_case_web_start.py",
    "scripts/hardware_case_fresh_extract_gate.py",
    "scripts/hardware_case_data_reliability.py",
    "INIT_LOCAL_CONFIG.bat",
    "CHECK_ENV.bat",
    "START_HARDWARE_CASE.bat",
    "RUN_REAL_AI_VALIDATION.bat",
    "INIT_LOCAL_CONFIG.sh",
    "START_HARDWARE_CASE.sh",
    "START_HARDWARE_CASE.command",
    "RUN_REAL_AI_VALIDATION.sh",
    "run_hardware_case_product_test.bat",
    "run_hardware_case_product_test.sh",
    "run_hardware_case_mvp_smoke.bat",
    "run_hardware_case_mvp_smoke.sh",
    "docs/product/HARDWARE_CASE_PRODUCT_TEST_FULL_V0.1.md",
]

# These are the executable Product Test entrypoints.  The closure scanner
# follows their top-level Python imports recursively; it deliberately does
# not walk unrelated lazy domain imports that are disabled by the
# HARDWARE_CASE-only composition profile.
CLOSURE_ROOTS = [
    "scripts/hardware_case_web_start.py",
    "scripts/hardware_case_precheck.py",
    "scripts/hardware_case_mvp_smoke.py",
    "scripts/hardware_case_product_test_smoke.py",
    "quality_knowledge/web/p0_app.py",
    "services/hardware_case_runtime_adapter.py",
]

LOCAL_IMPORT_PREFIXES = {
    "builder",
    "contracts",
    "models",
    "parser",
    "quality_knowledge",
    "repositories",
    "retriever",
    "runtime",
    "services",
}

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


def copy_relative(relative: str) -> None:
    source = ROOT / relative
    if not source.is_file():
        raise SystemExit(f"MISSING_REQUIRED_FILE={relative}")
    target = STAGE / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def copy_glob(pattern: str) -> None:
    matches = [path for path in ROOT.glob(pattern) if path.is_file() and allowed(path)]
    if not matches:
        raise SystemExit(f"MISSING_REQUIRED_GLOB={pattern}")
    for source in matches:
        relative = source.relative_to(ROOT)
        target = STAGE / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _module_name(path: Path, root: Path) -> str:
    relative = path.relative_to(root).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _module_path(module: str, root: Path) -> Path | None:
    if not module:
        return None
    candidate = root.joinpath(*module.split("."))
    file_candidate = candidate.with_suffix(".py")
    if file_candidate.is_file():
        return file_candidate
    package_candidate = candidate / "__init__.py"
    if package_candidate.is_file():
        return package_candidate
    return None


def _resolve_import(
    module: str,
    level: int,
    current_module: str,
    current_path: Path,
    root: Path,
) -> list[tuple[str, Path | None]]:
    if level:
        package = (
            current_module.split(".")
            if current_path.name == "__init__.py"
            else current_module.split(".")[:-1]
        )
        base = package[: len(package) - level + 1]
        resolved = ".".join(base + ([module] if module else []))
    else:
        resolved = module
    path = _module_path(resolved, root)
    return [(resolved, path)] if resolved else []


def _direct_imports(path: Path, root: Path) -> list[tuple[str, int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: list[tuple[str, int, str]] = []
    # Only top-level imports represent the HARDWARE_CASE startup profile.
    # Imports inside QUALITY_ISSUE/REPEAT_RISK branches are intentionally
    # excluded because those domains are not part of this product package.
    for node in tree.body:
        if isinstance(node, ast.Import):
            imports.extend((alias.name, 0, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                import_module = alias.name if not module and node.level else module
                imported = f"{module}.{alias.name}" if module else alias.name
                imports.append((import_module, node.level, imported))
    return imports


def dependency_closure(root: Path = ROOT) -> dict[str, object]:
    queue = [root / relative for relative in CLOSURE_ROOTS]
    visited: set[Path] = set()
    edges: list[dict[str, str]] = []
    unresolved: list[dict[str, str]] = []

    while queue:
        path = queue.pop(0).resolve()
        if path in visited:
            continue
        if not path.is_file():
            unresolved.append({"root": str(path.relative_to(root)), "reason": "ROOT_MISSING"})
            continue
        visited.add(path)
        current_module = _module_name(path, root)
        for module, level, imported in _direct_imports(path, root):
            for resolved_module, target in _resolve_import(
                module, level, current_module, path, root
            ):
                if target is not None:
                    edges.append({
                        "from": str(path.relative_to(root)),
                        "import": imported,
                        "module": resolved_module,
                        "target": str(target.relative_to(root)),
                    })
                    queue.append(target)
                elif resolved_module.split(".", 1)[0] in LOCAL_IMPORT_PREFIXES:
                    unresolved.append({
                        "from": str(path.relative_to(root)),
                        "module": resolved_module,
                        "import": imported,
                        "reason": "LOCAL_MODULE_MISSING",
                    })

    files = sorted(str(path.relative_to(root)) for path in visited)
    return {
        "scanner": "TOP_LEVEL_LOCAL_IMPORT_CLOSURE_V1",
        "roots": list(CLOSURE_ROOTS),
        "files": files,
        "edges": sorted(edges, key=lambda item: (item["from"], item["target"])),
        "unresolved_local_imports": unresolved,
        "status": "PASS" if not unresolved else "FAIL",
    }


def copy_dependency_closure() -> dict[str, object]:
    report = dependency_closure(ROOT)
    if report["status"] != "PASS":
        raise SystemExit(
            "DEPENDENCY_CLOSURE_FAIL="
            + json.dumps(report["unresolved_local_imports"], ensure_ascii=False)
        )
    for relative in report["files"]:
        copy_relative(str(relative))
    return report


def verify_staged_dependency_closure() -> dict[str, object]:
    report = dependency_closure(STAGE)
    if report["status"] != "PASS":
        raise SystemExit(
            "STAGED_DEPENDENCY_CLOSURE_FAIL="
            + json.dumps(report["unresolved_local_imports"], ensure_ascii=False)
        )
    return report


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
    cross_domain_forbidden = {
        "quality_knowledge/web/repeat_risk_integration.py",
        "quality_knowledge/web/api_v2.py",
        "quality_knowledge/web/p1_pages.py",
        "services/historical_case_contract.py",
        "services/knowledge_service.py",
        "repositories/json_repository.py",
        "main.py",
    }
    for entry in files:
        path = Path(str(entry["path"]))
        path_text = path.as_posix()
        if not allowed(path):
            forbidden.append(path_text)
        lower = path.name.lower()
        if lower in SENSITIVE_EXACT_NAMES or any(lower.endswith(p) for p in SENSITIVE_PATTERNS):
            forbidden.append(path_text)
        if path_text in cross_domain_forbidden or path_text.startswith("quality_knowledge/p0/"):
            forbidden.append(path_text)
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

    for pattern in INCLUDE_GLOBS:
        copy_glob(pattern)

    for relative in INCLUDE_FILES:
        copy_relative(relative)

    # Release wrappers must remain directly executable after ZIP extraction.
    for relative in (
        "INIT_LOCAL_CONFIG.sh",
        "START_HARDWARE_CASE.sh",
        "START_HARDWARE_CASE.command",
        "RUN_REAL_AI_VALIDATION.sh",
        "run_hardware_case_product_test.sh",
        "run_hardware_case_mvp_smoke.sh",
    ):
        path = STAGE / relative
        path.chmod(path.stat().st_mode | 0o111)

    closure = copy_dependency_closure()

    # Empty company-local working folders are intentionally created in the
    # package. Real data is supplied only after extraction inside the company.
    for relative in (
        "data/input/word",
        "data/tree",
        "data/output",
        "data/runtime",
        "data/evidence_sources",
        "data/hardware_case_sources",
    ):
        (STAGE / relative).mkdir(parents=True, exist_ok=True)

    staged_closure = verify_staged_dependency_closure()
    closure_report = {
        **closure,
        "staged_verification": {
            "status": staged_closure["status"],
            "file_count": len(staged_closure["files"]),
        },
    }
    (STAGE / "PACKAGE_DEPENDENCY_CLOSURE.json").write_text(
        json.dumps(closure_report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    files = inventory()
    security_assertions(files)
    source_commit = (
        os.getenv("HARDWARE_RELEASE_SOURCE_COMMIT")
        or os.getenv("GITHUB_SHA")
        or "LOCAL"
    )
    short = source_commit[:12] if source_commit != "LOCAL" else "LOCAL"
    archive_name = f"{PACKAGE_NAME}_{PACKAGE_ARCHIVE_VARIANT}_{short}.zip"

    manifest = {
        "package": PACKAGE_NAME,
        "candidate_archive": archive_name,
        "product": "HARDWARE_CASE",
        "target_version": "MVP_V0.1",
        "product_version": "MVP_V0.1",
        "schema_version": "HARDWARE_SCHEMA_V1",
        "package_revision": "FULL_V0.1_P01_P07",
        "package_status": "READY_FOR_INTERNAL_TEST",
        "release_status": "TEST_PACKAGE_NOT_RELEASE",
        "source_commit": source_commit,
        "contract_version": "hardware-case/v1",
        "tree_import_contract_version": "hardware-tree-import/v1",
        "public_contract_version": "hardware-public-consumer/v1",
        "web_entry": "/p0/hardware-cases",
        "api_prefix": "/api/v2/hardware-cases",
        "public_api_prefix": "/api/public/hardware/v1",
        "system_endpoints": {
            "health": "/health",
            "readiness": "/ready",
            "contract_descriptor": "/api/public/hardware/v1/contract",
        },
        "entrypoints": {
            "init_local_config_windows": "INIT_LOCAL_CONFIG.bat",
            "precheck_windows": "CHECK_ENV.bat",
            "start_product_windows": "START_HARDWARE_CASE.bat",
            "web_launcher": "scripts/hardware_case_web_start.py",
            "real_ai_validation_windows": "RUN_REAL_AI_VALIDATION.bat",
            "start_product_shell": "START_HARDWARE_CASE.sh",
            "start_product_macos": "START_HARDWARE_CASE.command",
            "real_ai_validation_shell": "RUN_REAL_AI_VALIDATION.sh",
        },
        "release_semantics": {
            "source_binding": "EXACT_SOURCE_COMMIT",
            "contract_version_independent_from_product_version": True,
            "cross_platform": "SAME_SOURCE_CONTRACT_SCHEMA_STARTUP_HEALTH_READINESS",
            "fresh_extract_gate_required": True,
        },
        "config_lifecycle": {
            "precedence": ["ENV_SECRET_REFERENCE", "LOCAL_CONFIG", "PACKAGED_NON_SECRET_DEFAULT"],
            "secret_in_package": False,
            "missing_required_config": "FAIL_CLOSED",
            "windows_macos_semantics": "SAME",
        },
        "data_reliability": {
            "schema_version": "HARDWARE_SCHEMA_V1",
            "migration_policy": "VERSIONED_DETERMINISTIC_ATOMIC",
            "backup_before_migration": "MANDATORY_FOR_EXISTING_DB",
            "unknown_schema": "FAIL_CLOSED",
            "restore": "VERIFIABLE_TRACEABLE",
            "recovery_state": "READINESS_BLOCKING",
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
            "P01 Hardware Case homepage",
            "P02 Circuit/Feature and Material/Device dual-tree navigation",
            "P03 published-case search",
            "P04 engineering case detail",
            "P05 maintainer review + Publish Gate",
            "P06 Evidence Viewer + company-local Source Resolver",
            "P07 base-data management frontend",
            "Circuit/Feature and Material/Device tree import workflow",
            "Excel upload / Sheet+Header / dynamic Mapping / Preview / Validation",
            "Change Diff / Conflict / EXCLUDE / Atomic Apply / Tree Version / History",
            "Hardware Case backend/API publish and consume Golden Path",
            "DOCX parser + AI Adapter boundary",
            "Hardware Case Agent Config + Unified Runtime Adapter",
            "Local model configuration template and environment precheck",
            "One-click Web start through the same create_p0_app with HARDWARE_CASE-only domain composition",
            "Packaged startup import check from the built artifact",
            "Company-local Real AI validation entry",
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
        "domain_boundary": {
            "composition_profile": ["HARDWARE_CASE"],
            "platform_shared": ["runtime"],
            "package_policy": "EXPLICIT_HARDWARE_ALLOWLIST",
            "cross_domain_business_code_bundled": False,
        },
        "known_gaps": [
            "Real company Word/Excel data is not bundled",
            "Real Provider acceptance still requires company-environment validation with approved endpoint/model",
            "The repository-wide main.py CLI is intentionally not packaged; Hardware Case uses the dedicated launcher to avoid unrelated legacy builder dependencies",
        ],
        "frontend_gate": "P01_P07_FRONTEND_GATE_PASS",
        "explicitly_not_claimed": [
            "REAL_DATA_VALIDATED",
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
        "dependency_closure": {
            "report": "PACKAGE_DEPENDENCY_CLOSURE.json",
            "status": closure_report["status"],
            "scanner": closure_report["scanner"],
            "file_count": len(closure_report["files"]),
            "staged_verification": closure_report["staged_verification"],
        },
        "files": files,
    }

    manifest_path = STAGE / "PACKAGE_MANIFEST.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    archive = DIST / archive_name
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
