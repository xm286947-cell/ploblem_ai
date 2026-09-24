from __future__ import annotations

import hashlib
import json
import os
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
PACKAGE_NAME = "HARDWARE_CASE_MVP_RC0_PREP"
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
    "main.py",
    "requirements.txt",
    "requirements-runtime-p0-test.txt",
    "start_quality_capability_p1.bat",
    "config/hardware_case_real_validation.example.json",
    "tools/hardware_case_real_validation.py",
    "scripts/hardware_case_mvp_smoke.py",
    "run_hardware_case_mvp_smoke.bat",
    "run_hardware_case_mvp_smoke.sh",
    "docs/product/HARDWARE_CASE_MVP_RC0_PREP.md",
]

EXCLUDED_NAMES = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".DS_Store",
    ".git",
}
EXCLUDED_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".log",
    ".zip",
}
SENSITIVE_EXACT_NAMES = {
    ".env",
    ".env.local",
    "model.local.yaml",
    "model.local.yml",
    "agent.local.yaml",
    "agent.local.yml",
}
SENSITIVE_PATTERNS = (
    ".local.yaml",
    ".local.yml",
    ".secret.yaml",
    ".secret.yml",
)


def allowed(path: Path) -> bool:
    if any(part in EXCLUDED_NAMES for part in path.parts):
        return False
    if path.suffix.lower() in EXCLUDED_SUFFIXES:
        return False
    lower_name = path.name.lower()
    if lower_name in SENSITIVE_EXACT_NAMES:
        return False
    if any(lower_name.endswith(pattern) for pattern in SENSITIVE_PATTERNS):
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


def _inventory() -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for item in sorted(STAGE.rglob("*")):
        if not item.is_file():
            continue
        items.append(
            {
                "path": item.relative_to(STAGE).as_posix(),
                "sha256": sha256(item),
                "size": item.stat().st_size,
            }
        )
    return items


def _security_assertions(files: list[dict[str, object]]) -> None:
    forbidden = []
    for entry in files:
        path = str(entry["path"])
        candidate = Path(path)
        if not allowed(candidate):
            forbidden.append(path)
        lower = candidate.name.lower()
        if lower in SENSITIVE_EXACT_NAMES or any(
            lower.endswith(pattern) for pattern in SENSITIVE_PATTERNS
        ):
            forbidden.append(path)
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

    files = _inventory()
    _security_assertions(files)

    source_commit = os.getenv("GITHUB_SHA", "LOCAL")
    manifest = {
        "package": PACKAGE_NAME,
        "product": "HARDWARE_CASE",
        "target_version": "MVP_V0.1",
        "release_status": "PREP_ONLY_NOT_MVP_RELEASE",
        "source_commit": source_commit,
        "contract_version": "hardware-case/v1",
        "api_prefix": "/api/v2/hardware-cases",
        "golden_path": (
            "Source -> Candidate -> Human Confirm -> Evidence -> Mapping -> "
            "Publish -> Search/Tree -> Detail -> Evidence"
        ),
        "included_capabilities": [
            "hardware-case/v1 contract",
            "SQLite Hardware Case repository",
            "Backend Publish Gate and consumer visibility rules",
            "DOCX parser and AI Adapter boundary",
            "Unified create_p0_app /api/v2 Hardware Case API",
            "Company-only Real Validation Harness",
            "Offline synthetic package smoke",
        ],
        "release_blockers": [
            "HIFI_PRODUCT_GATE_PASS",
            "FRONTEND_MVP_GATE_PASS",
            "M4_REAL_DATA_VALIDATED",
            "AI_INTEGRATION_GATE_PASS",
            "20_TO_30_REAL_CASE_MVP_INTEGRATION_GATE_PASS",
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
            ".env and local/secret YAML configuration",
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
    checksum_path = archive.with_suffix(archive.suffix + ".sha256")
    checksum_path.write_text(
        f"{checksum}  {archive.name}\n",
        encoding="utf-8",
    )

    print(f"PACKAGE={archive}")
    print(f"SHA256={checksum}")
    print(f"SIZE={archive.stat().st_size}")
    print(f"FILES={len(files) + 1}")
    print("RELEASE_STATUS=PREP_ONLY_NOT_MVP_RELEASE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
