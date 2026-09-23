from __future__ import annotations

import hashlib
import json
import os
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
STAGE = DIST / "CASE_LIBRARY_MVP_RC1_PATCH02_WINDOWS_SQLITE"

PATCH_FILES = [
    "quality_knowledge/major_cases/repository.py",
    "tests/test_major_repository_connection_lifecycle.py",
    "CASE_LIBRARY_MVP_RC1_PATCH02_WINDOWS_SQLITE.md",
]


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

    for relative in PATCH_FILES:
        src = ROOT / relative
        if not src.is_file():
            raise SystemExit(f"MISSING_PATCH_FILE={relative}")
        dst = STAGE / relative
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    source_commit = os.getenv("GITHUB_SHA", "LOCAL")
    manifest = {
        "patch": "CASE_LIBRARY_MVP_RC1_PATCH02_WINDOWS_SQLITE",
        "base": "CASE_LIBRARY_MVP_RC1",
        "issue": "WINDOWS_SQLITE_HANDLE_NOT_RELEASED",
        "runtime_semantics_changed": False,
        "major_schema_changed": False,
        "historical_case_contract_changed": False,
        "purpose": "Release SQLite file handles deterministically so Windows TemporaryDirectory cleanup succeeds.",
        "apply": "Overlay files at the same relative paths, then rerun run_major_case_mvp_smoke.bat.",
        "source_commit": source_commit,
        "files": PATCH_FILES,
    }
    (STAGE / "PATCH_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    short = source_commit[:12] if source_commit != "LOCAL" else "LOCAL"
    archive = DIST / f"CASE_LIBRARY_MVP_RC1_PATCH02_WINDOWS_SQLITE_{short}.zip"
    if archive.exists():
        archive.unlink()

    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for item in sorted(STAGE.rglob("*")):
            if item.is_file():
                zf.write(item, item.relative_to(DIST).as_posix())

    digest = sha256(archive)
    checksum = archive.with_suffix(archive.suffix + ".sha256")
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")

    print(f"PATCH={archive}")
    print(f"SHA256={digest}")
    print(f"SIZE={archive.stat().st_size}")
    print(f"FILES={len(PATCH_FILES) + 1}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
