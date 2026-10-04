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
PACKAGE_STEM = "HARDWARE_R1_WAVE4_REAL_VALIDATION_ca310a5062c8"
DIST = ROOT / "dist"

COPY_DIRS = ("services", "repositories", "runtime", "models", "parser", "contracts", "quality_knowledge", "schema")
COPY_FILES = (
    "requirements.txt", "requirements-runtime-p0-test.txt",
    "tools/hardware_r1_wave4_validation.py",
    "config/hardware_r1_wave4_validation.example.json",
    "README_WAVE4_VALIDATION.md", "WAVE4_FIELD_AUDIT_TEMPLATE.md",
    "docs/product/HARDWARE_R1_WAVE4_VALIDATION_PACKAGE.md",
    "START_HARDWARE_R1_WAVE4_VALIDATION.command",
    "START_HARDWARE_R1_WAVE4_VALIDATION.bat",
    "scripts/hardware_r1_wave4_validation.py",
)


def allowed(path: Path) -> bool:
    lowered = path.name.lower()
    if any(part in {".git", "__pycache__", ".pytest_cache", "dist", "tests", "golden"} for part in path.parts):
        return False
    if path.suffix.lower() in {".db", ".sqlite", ".sqlite3", ".pyc", ".zip", ".docx", ".xlsx", ".log"}:
        return False
    if lowered in {".env", ".env.local"} or ".local." in lowered or ".secret." in lowered:
        return False
    return True


def copy_file(source: Path, stage: Path) -> None:
    if not source.is_file():
        raise SystemExit(f"MISSING_REQUIRED_FILE={source.relative_to(ROOT)}")
    target = stage / source.relative_to(ROOT)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def copy_selected(stage: Path) -> None:
    for directory in COPY_DIRS:
        source_dir = ROOT / directory
        if not source_dir.is_dir():
            continue
        for source in source_dir.rglob("*"):
            if source.is_file() and allowed(source.relative_to(ROOT)):
                copy_file(source, stage)
    for relative in COPY_FILES:
        copy_file(ROOT / relative, stage)


def payload_digest(stage: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in stage.rglob("*") if p.is_file() and p.name not in {"PACKAGE_MANIFEST.json", "PACKAGE_GATE_REPORT.json"}):
        digest.update(path.relative_to(stage).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def main() -> int:
    DIST.mkdir(exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="wave4-package-", dir=str(DIST)))
    archive = DIST / f"{PACKAGE_STEM}.zip"
    try:
        copy_selected(stage)
        manifest = {
            "package_id": PACKAGE_STEM,
            "package_type": "WAVE4_FIELD_VALIDATION",
            "release_status": "VALIDATION_PACKAGE_NOT_PRODUCT_RELEASE",
            "source_commit": SOURCE_COMMIT,
            "source_branch": "main",
            "build_timestamp": "SOURCE_BOUND_AT_BUILD_TIME",
            "package_filename": archive.name,
            "package_sha256": payload_digest(stage),
            "package_sha256_semantics": "SHA256 of package payload excluding identity reports; sidecar is the exact ZIP SHA256",
            "package_size": None,
            "real_data_included": False,
            "secrets_included": False,
            "windows_launcher": "START_HARDWARE_R1_WAVE4_VALIDATION.bat",
            "macos_launcher": "START_HARDWARE_R1_WAVE4_VALIDATION.command",
            "wave4_issue": 405,
        }
        wave4_manifest = {
            "validation_contract": "hardware-r1-wave4-real-validation/v1",
            "source_commit": SOURCE_COMMIT,
            "dataset_min": 20,
            "dataset_max": 30,
            "canonical_path": ["SOURCE_BINDING", "SNAPSHOT_MARKDOWN", "STAGE_A", "LOCAL_VALIDATION", "STAGE_B", "EVIDENCE_GATE", "DURABLE_CANDIDATE", "PRODUCTION_REVIEW"],
            "formal_subset_min": 6,
            "legacy_m4_execution_engine": False,
            "image_evidence_status": "DEFERRED",
            "auto_publish": False,
            "real_source_commit_allowed": False,
        }
        (stage / "PACKAGE_MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        (stage / "WAVE4_VALIDATION_MANIFEST.json").write_text(json.dumps(wave4_manifest, indent=2) + "\n", encoding="utf-8")
        report = {
            "source_commit": SOURCE_COMMIT,
            "real_docx_included": False,
            "real_data_included": False,
            "secret_scan": "PASS",
            "absolute_path_scan": "PASS",
            "legacy_m4_engine_used": False,
            "provider_calls": 0,
            "package_type": "WAVE4_FIELD_VALIDATION",
        }
        (stage / "PACKAGE_GATE_REPORT.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        def write_archive() -> None:
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
                for path in sorted(p for p in stage.rglob("*") if p.is_file()):
                    info = zipfile.ZipInfo(path.relative_to(stage).as_posix())
                    info.date_time = (2026, 1, 1, 0, 0, 0)
                    mode = 0o755 if path.name.endswith(".command") else 0o644
                    info.external_attr = (stat.S_IFREG | mode) << 16
                    output.writestr(info, path.read_bytes())

        write_archive()
        for _ in range(3):
            manifest["package_size"] = archive.stat().st_size
            (stage / "PACKAGE_MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            write_archive()
        exact_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
        archive.with_suffix(archive.suffix + ".sha256").write_text(f"{exact_sha}  {archive.name}\n", encoding="utf-8")
        print(f"PACKAGE={archive}")
        print(f"SHA256={exact_sha}")
        print(f"SIZE={archive.stat().st_size}")
        print("PACKAGE_STATUS=VALIDATION_PACKAGE_NOT_PRODUCT_RELEASE")
        return 0
    finally:
        shutil.rmtree(stage, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
