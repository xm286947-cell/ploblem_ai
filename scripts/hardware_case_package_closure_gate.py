from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_hardware_case_product_test_package import (
    ASSET_MIGRATION_MODULE_DIR,
    REQUIRED_ASSET_MIGRATION_MODULES,
    STAGE,
    asset_migration_module_paths,
)


def _python_module_name(relative: str) -> str:
    module = Path(relative).with_suffix("").as_posix().replace("/", ".")
    if module.endswith(".__init__"):
        module = module[: -len(".__init__")]
    return module


def main() -> int:
    manifest_path = STAGE / "PACKAGE_MANIFEST.json"
    closure_path = STAGE / "PACKAGE_DEPENDENCY_CLOSURE.json"
    if not manifest_path.is_file() or not closure_path.is_file():
        raise SystemExit("PACKAGE_MANIFEST_OR_CLOSURE_MISSING")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    closure = json.loads(closure_path.read_text(encoding="utf-8"))
    expected = asset_migration_module_paths(ROOT)
    package_contract = manifest.get("asset_migration_package") or {}
    package_paths = {item.get("path") for item in manifest.get("files", [])}
    closure_paths = set(closure.get("files", []))
    closure_roots = set(closure.get("roots", []))

    if package_contract.get("module_dir") != ASSET_MIGRATION_MODULE_DIR:
        raise SystemExit("PACKAGE_ASSET_MIGRATION_DIR_MISMATCH")
    if package_contract.get("status") != "PASS":
        raise SystemExit("PACKAGE_ASSET_MIGRATION_STATUS_FAIL")
    if package_contract.get("required_modules") != sorted(REQUIRED_ASSET_MIGRATION_MODULES):
        raise SystemExit("PACKAGE_ASSET_MIGRATION_REQUIREMENTS_MISMATCH")
    if package_contract.get("modules") != expected:
        raise SystemExit("PACKAGE_ASSET_MIGRATION_MANIFEST_MISMATCH")
    if not set(expected).issubset(package_paths):
        raise SystemExit("PACKAGE_ASSET_MIGRATION_FILES_MISSING")
    if not set(expected).issubset(closure_paths) or not set(expected).issubset(closure_roots):
        raise SystemExit("PACKAGE_ASSET_MIGRATION_CLOSURE_MISSING")
    if closure.get("status") != "PASS":
        raise SystemExit("PACKAGE_DEPENDENCY_CLOSURE_FAIL")
    staged = closure.get("staged_verification") or {}
    if staged.get("status") != "PASS" or staged.get("file_count") != staged.get("expected_file_count"):
        raise SystemExit("PACKAGE_STAGED_CLOSURE_INCOMPLETE")
    for relative in expected:
        if not (STAGE / relative).is_file():
            raise SystemExit("PACKAGE_STAGED_MIGRATION_MISSING=" + relative)

    archive_name = str(manifest.get("candidate_archive") or "")
    archive_path = ROOT / "dist" / archive_name
    if not archive_name or not archive_path.is_file():
        raise SystemExit("PACKAGE_ARCHIVE_MISSING")
    with zipfile.ZipFile(archive_path) as archive:
        archive_paths = set(archive.namelist())
    archive_prefix = STAGE.name + "/"
    missing_from_archive = [
        relative for relative in expected if archive_prefix + relative not in archive_paths
    ]
    if missing_from_archive:
        raise SystemExit("PACKAGE_ARCHIVE_MIGRATIONS_MISSING=" + ",".join(missing_from_archive))

    import_code = [
        "import importlib",
        "importlib.import_module('services.hardware_asset_repository')",
    ]
    import_code.extend(
        "importlib.import_module(%r)" % _python_module_name(relative)
        for relative in expected
    )
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, "-c", ";".join(import_code)],
        cwd=STAGE,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        detail = (result.stdout + result.stderr)[-1600:].replace("\n", " ")
        raise SystemExit("PACKAGE_ASSET_MIGRATION_IMPORT_FAIL=" + detail)

    print("ASSET_MIGRATION_MODULES_PACKAGED=PASS")
    print("PACKAGE_DEPENDENCY_CLOSURE=PASS")
    print("PACKAGE_ASSET_REPOSITORY_IMPORT=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
