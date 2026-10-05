from __future__ import annotations

import hashlib
import json
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_COMMIT = "ca310a5062c8fa15e6a00d22c718fdc7504192c8"
PREPARE_SHA256 = "d8305b991ffc40b43c72d8aefde64db13c0128784efc75a5286cefc7c80621e0"
PACKAGE_NAME = "HARDWARE_R1_WAVE4_REAL_EXECUTION_ca310a5062c8.zip"
DIST = ROOT / "dist"
COPY_DIRS = ("services", "repositories", "runtime", "models", "parser", "contracts", "quality_knowledge", "schema", "config", "prompts")
COPY_FILES = (
    "requirements.txt", "requirements-runtime-p0-test.txt",
    "tools/hardware_r1_wave4_execution.py",
    "RUN_HARDWARE_R1_WAVE4_REAL_EXECUTION.bat",
    "RUN_HARDWARE_R1_WAVE4_REAL_EXECUTION.command",
    "README_WAVE4_REAL_EXECUTION.md",
    "WAVE4_FIELD_AUDIT_TEMPLATE.md",
    "config/hardware_r1_wave4_execution.example.json",
)
EXCLUDED = {".git", "__pycache__", ".pytest_cache", "dist", "tests", "golden"}
FORBIDDEN_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".pyc", ".zip", ".docx", ".xlsx", ".log"}


def allowed(path: Path) -> bool:
    name = path.name.lower()
    return not (
        any(part in EXCLUDED for part in path.parts)
        or path.suffix.lower() in FORBIDDEN_SUFFIXES
        or name in {".env", ".env.local"}
        or ".secret." in name
        or ".local." in name
    )


def copy_file(source: Path, stage: Path) -> None:
    if not source.is_file():
        raise SystemExit(f"MISSING_REQUIRED_FILE={source.relative_to(ROOT)}")
    target = stage / source.relative_to(ROOT)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def main() -> int:
    DIST.mkdir(exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="wave4-execution-package-", dir=DIST))
    archive = DIST / PACKAGE_NAME
    try:
        for directory in COPY_DIRS:
            source_root = ROOT / directory
            for source in source_root.rglob("*"):
                if source.is_file() and allowed(source.relative_to(ROOT)):
                    copy_file(source, stage)
        for relative in COPY_FILES:
            copy_file(ROOT / relative, stage)

        manifest = {
            "package_id": PACKAGE_NAME.removesuffix(".zip"),
            "package_type": "WAVE4_REAL_EXECUTION",
            "release_status": "VALIDATION_EXECUTION_PACKAGE_NOT_PRODUCT_RELEASE",
            "source_product_commit": SOURCE_COMMIT,
            "prepare_package_sha256": PREPARE_SHA256,
            "real_data_included": False,
            "secrets_included": False,
            "real_provider_capable": True,
            "real_provider_default_enabled": False,
            "web_service_required": False,
            "auto_publish": False,
            "package_size": None,
        }
        gate_report = {
            "source_product_commit": SOURCE_COMMIT,
            "prepare_package_sha256": PREPARE_SHA256,
            "real_docx_included": False,
            "real_data_included": False,
            "secrets_included": False,
            "provider_calls": 0,
            "auto_review": False,
            "auto_publish": False,
        }
        (stage / "EXECUTION_PACKAGE_MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        (stage / "EXECUTION_PACKAGE_GATE_REPORT.json").write_text(json.dumps(gate_report, indent=2) + "\n", encoding="utf-8")

        def write_zip() -> None:
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
                for path in sorted(item for item in stage.rglob("*") if item.is_file()):
                    info = zipfile.ZipInfo(path.relative_to(stage).as_posix())
                    info.create_system = 3
                    info.date_time = (2026, 1, 1, 0, 0, 0)
                    mode = 0o755 if path.name.endswith(".command") else 0o644
                    info.external_attr = (stat.S_IFREG | mode) << 16
                    output.writestr(info, path.read_bytes())

        write_zip()
        for _ in range(3):
            manifest["package_size"] = archive.stat().st_size
            (stage / "EXECUTION_PACKAGE_MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            write_zip()
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        archive.with_suffix(".zip.sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
        print(f"PACKAGE={archive}")
        print(f"SHA256={digest}")
        print(f"SIZE={archive.stat().st_size}")
        return 0
    finally:
        shutil.rmtree(stage, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
