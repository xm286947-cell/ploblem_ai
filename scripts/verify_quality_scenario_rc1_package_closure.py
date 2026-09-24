from __future__ import annotations

import argparse
import ast
import hashlib
from pathlib import Path


EXCLUDED_BASELINE_TOPS = {
    ".git",
    ".github",
    ".pytest_cache",
    "__pycache__",
    ".deps",
    "tests",
    "knowledge",
    "input",
    "output",
    "outputs",
    "baseline_release",
    "releases",
    "deliverables",
    "docs",
    "scripts",
}

HASH_COMPARE_SUFFIXES = {
    ".py", ".html", ".js", ".css", ".yaml", ".yml", ".json", ".sql", ".txt", ".md"
}

PACKAGE_SOURCE_COMPARE_EXCLUDES = {
    "config/runtime/model.yaml",  # supplied by the separately frozen Runtime baseline
    "PACKAGE_MANIFEST.json",
    "README_TEST_PACKAGE.md",
    "run_windows.bat",
    "install_windows.bat",
    "verify_windows.bat",
    "prepare_internal_golden.bat",
}

REQUIRED_ASSETS = (
    "models/common.py",
    "quality_knowledge/models/issue.py",
    "quality_knowledge/p0/schema.sql",
    "quality_knowledge/config/p0_seed_manifest.json",
    "quality_knowledge/config/plc_fields.yaml",
    "quality_knowledge/web/templates/p0_quality_scenario_workbench.html",
    "quality_knowledge/web/templates/p0_quality_scenario_library.html",
    "quality_knowledge/web/templates/p0_quality_scenario_detail.html",
    "quality_knowledge/web/static/p0_scenario_workbench.js",
    "quality_knowledge/web/static/p0_scenario_library.js",
    "quality_knowledge/web/static/p0_scenario_detail.js",
    "config/runtime/agents/reverse_quality.single_issue.analyze.yaml",
    "config/runtime/model.yaml",
    "prompts/runtime/reverse_quality/single_issue_v01.md",
    "vendor/unified_agent_runtime/runtime/__init__.py",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def internal_top_levels(root: Path) -> set[str]:
    result: set[str] = set()
    for child in root.iterdir():
        if child.name in EXCLUDED_BASELINE_TOPS:
            continue
        if child.is_file() and child.suffix == ".py":
            result.add(child.stem)
        elif child.is_dir() and any(child.rglob("*.py")):
            result.add(child.name)
    return result


def module_source_path(root: Path, module: str) -> Path | None:
    parts = module.split(".")
    py = root.joinpath(*parts).with_suffix(".py")
    if py.is_file():
        return py
    package_init = root.joinpath(*parts, "__init__.py")
    if package_init.is_file():
        return package_init
    return None


def module_package_path(package_root: Path, baseline_root: Path, module: str) -> Path | None:
    source = module_source_path(baseline_root, module)
    if source is None:
        return None
    rel = source.relative_to(baseline_root)
    return package_root / rel


def import_modules(path: Path) -> list[tuple[str, list[str]]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, SyntaxError) as error:
        raise RuntimeError(f"PYTHON_PARSE_FAILED:{path}:{error}") from error

    result: list[tuple[str, list[str]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                result.append((alias.name, []))
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            result.append((node.module, [alias.name for alias in node.names]))
    return result


def verify_import_closure(baseline_root: Path, package_root: Path) -> tuple[list[str], list[str]]:
    internal = internal_top_levels(baseline_root)
    missing: set[str] = set()
    referenced: set[str] = set()

    for py in sorted(package_root.rglob("*.py")):
        if "vendor/unified_agent_runtime" in py.as_posix():
            continue
        for module, names in import_modules(py):
            top = module.split(".")[0]
            if top not in internal:
                continue
            referenced.add(module)
            target = module_package_path(package_root, baseline_root, module)
            if target is not None and not target.is_file():
                missing.add(target.relative_to(package_root).as_posix())

            # Handles "from models import common" style imports where a name
            # is itself a source submodule.
            for name in names:
                if name == "*":
                    continue
                submodule = f"{module}.{name}"
                subtarget = module_package_path(package_root, baseline_root, submodule)
                if subtarget is not None:
                    referenced.add(submodule)
                    if not subtarget.is_file():
                        missing.add(subtarget.relative_to(package_root).as_posix())

    return sorted(referenced), sorted(missing)


def verify_required_assets(package_root: Path) -> list[str]:
    return sorted(rel for rel in REQUIRED_ASSETS if not (package_root / rel).is_file())


def verify_baseline_hashes(baseline_root: Path, package_root: Path) -> list[str]:
    mismatches: list[str] = []
    for packaged in sorted(package_root.rglob("*")):
        if not packaged.is_file():
            continue
        rel = packaged.relative_to(package_root).as_posix()
        if rel in PACKAGE_SOURCE_COMPARE_EXCLUDES:
            continue
        if rel.startswith("vendor/unified_agent_runtime/") or rel.startswith("tools/"):
            continue
        if packaged.suffix.lower() not in HASH_COMPARE_SUFFIXES:
            continue
        source = baseline_root / rel
        if not source.is_file():
            continue
        if sha256(source) != sha256(packaged):
            mismatches.append(rel)
    return mismatches


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-root", required=True)
    parser.add_argument("--package-root", required=True)
    args = parser.parse_args()

    baseline_root = Path(args.baseline_root).resolve()
    package_root = Path(args.package_root).resolve()
    if not baseline_root.is_dir():
        raise SystemExit("BASELINE_ROOT_NOT_FOUND")
    if not package_root.is_dir():
        raise SystemExit("PACKAGE_ROOT_NOT_FOUND")

    referenced, missing_imports = verify_import_closure(baseline_root, package_root)
    missing_assets = verify_required_assets(package_root)
    hash_mismatches = verify_baseline_hashes(baseline_root, package_root)

    missing = sorted(set(missing_imports + missing_assets))
    print("INTERNAL_IMPORT_MODULE_COUNT=" + str(len(referenced)))
    print("INTERNAL_IMPORT_MODULES=" + ",".join(referenced))
    print("MISSING_RUNTIME_FILE=" + str(len(missing)))
    if missing:
        print("MISSING_RUNTIME_FILES=" + ",".join(missing))
    print("BASELINE_RUNTIME_HASH_MISMATCH=" + str(len(hash_mismatches)))
    if hash_mismatches:
        print("BASELINE_RUNTIME_HASH_MISMATCH_FILES=" + ",".join(hash_mismatches))

    if missing or hash_mismatches:
        print("PACKAGE_DEPENDENCY_CLOSURE=FAIL")
        return 1

    print("PACKAGE_DEPENDENCY_CLOSURE=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
