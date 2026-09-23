from __future__ import annotations

import hashlib
import json
import os
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
STAGE = DIST / "CASE_LIBRARY_MVP_RC1"

INCLUDE_DIRS = [
    "builder",
    "config",
    "contracts",
    "parser",
    "quality_knowledge/major_cases",
    "repositories",
    "retriever",
    "services",
    "schema",
]

INCLUDE_FILES = [
    "quality_knowledge/__init__.py",
    "quality_knowledge/sqlite_tuning.py",
    "requirements.txt",
    "requirements-runtime-p0-test.txt",
    "docs/consumers/HISTORICAL_CASE_V1.md",
    "scripts/major_case_mvp_smoke.py",
    "run_major_case_mvp_smoke.bat",
    "run_major_case_mvp_smoke.sh",
    "MAJOR_CASE_MVP_RC1_DELIVERY.md",
    "docs/product/CASE_LIBRARY_PRODUCT_BOUNDARY_V1.md",
]

EXCLUDED_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".DS_Store"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".sqlite", ".sqlite3", ".db", ".log"}


def allowed(path: Path) -> bool:
    if any(part in EXCLUDED_NAMES for part in path.parts):
        return False
    if path.suffix.lower() in EXCLUDED_SUFFIXES:
        return False
    name = path.name.lower()
    if name in {".env", ".env.local"} or name.endswith(".local.yaml") or name.endswith(".local.yml"):
        return False
    return True


def copy_tree(src: Path, dst: Path) -> None:
    for item in src.rglob("*"):
        if not item.is_file() or not allowed(item):
            continue
        rel = item.relative_to(src)
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    shutil.rmtree(STAGE, ignore_errors=True)
    DIST.mkdir(parents=True, exist_ok=True)
    STAGE.mkdir(parents=True, exist_ok=True)

    copied: list[str] = []
    for relative in INCLUDE_DIRS:
        src = ROOT / relative
        if not src.exists():
            raise SystemExit(f"MISSING_REQUIRED_DIR={relative}")
        copy_tree(src, STAGE / relative)

    for relative in INCLUDE_FILES:
        src = ROOT / relative
        if not src.is_file():
            raise SystemExit(f"MISSING_REQUIRED_FILE={relative}")
        dst = STAGE / relative
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    for item in sorted(STAGE.rglob("*")):
        if item.is_file():
            copied.append(item.relative_to(STAGE).as_posix())

    source_commit = os.getenv("GITHUB_SHA", "LOCAL")
    manifest = {
        "package": "CASE_LIBRARY_MVP_RC1",
        "product": "CASE_LIBRARY",
        "product_positioning": "ONE_CASE_LIBRARY_MULTIPLE_KNOWLEDGE_SOURCES",
        "source_channel": "MAJOR_EVENT",
        "standalone_major_case_product": False,
        "source_commit": source_commit,
        "contract_version": "historical-case/v1",
        "golden_path": "Major Confirmed -> Publish -> Search -> Detail -> Evidence",
        "scope": [
            "Unified Historical Case assets and consumer contract",
            "Major Event source channel via CASE-PUBLISH",
            "Major Knowledge Repository + independent SQLite schema",
            "CASE-PUBLISH-001 adapter and publisher",
            "Historical Case consumer contract",
            "offline smoke validation assets",
        ],
        "excluded": [
            "runtime business data",
            "input/output/knowledge runtime data",
            "secrets and local config",
            "Repeat Risk decision logic",
            "Major web/UI",
        ],
        "files": copied,
    }
    (STAGE / "PACKAGE_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    short = source_commit[:12] if source_commit != "LOCAL" else "LOCAL"
    archive = DIST / f"CASE_LIBRARY_MVP_RC1_{short}.zip"
    if archive.exists():
        archive.unlink()

    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for item in sorted(STAGE.rglob("*")):
            if item.is_file():
                zf.write(item, item.relative_to(DIST).as_posix())

    digest = sha256(archive)
    checksum = archive.with_suffix(archive.suffix + ".sha256")
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")

    print(f"PACKAGE={archive}")
    print(f"SHA256={digest}")
    print(f"SIZE={archive.stat().st_size}")
    print(f"FILES={len(copied) + 1}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
