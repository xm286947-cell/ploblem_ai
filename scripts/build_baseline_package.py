from __future__ import annotations

import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / "BASELINE_VERSION").read_text(encoding="utf-8").strip()
OUTPUT = ROOT / "baseline_release"
PACKAGE_ROOT = f"KNOWLEDGE_QUALITY_ISSUE_ANALYSIS_ENGINE_{VERSION}"

EXCLUDED_TOP_LEVEL = {
    ".git", ".pytest_cache", "__pycache__", "baseline_release", "releases",
    "deliverables",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".db", ".sqlite", ".sqlite3", ".log", ".zip"}


def included_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT)
        if relative.parts[0] in EXCLUDED_TOP_LEVEL:
            continue
        if any(part in {"__pycache__", ".pytest_cache"} for part in relative.parts):
            continue
        if relative.parts[0] == "output" and path.name != ".gitkeep":
            continue
        if relative.parts[:2] in {("knowledge", "raw_evidence"), ("knowledge", "raw_excel")} and path.name != ".gitkeep":
            continue
        if path.suffix.lower() in EXCLUDED_SUFFIXES or path.name == ".DS_Store":
            continue
        files.append(path)
    return sorted(files, key=lambda item: item.as_posix())


def main() -> None:
    OUTPUT.mkdir(exist_ok=True)
    files = included_files()
    manifest = {
        "version": VERSION,
        "file_count": len(files),
        "files": [
            {
                "path": path.relative_to(ROOT).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size": path.stat().st_size,
            }
            for path in files
        ],
    }
    manifest_path = OUTPUT / f"{VERSION}_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    archive_path = OUTPUT / f"KNOWLEDGE_QUALITY_ISSUE_ANALYSIS_ENGINE_{VERSION}_FULL.zip"
    with ZipFile(archive_path, "w", ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, f"{PACKAGE_ROOT}/{path.relative_to(ROOT).as_posix()}")
        archive.writestr(
            f"{PACKAGE_ROOT}/BASELINE_MANIFEST.json",
            json.dumps(manifest, ensure_ascii=False, indent=2),
        )
    print(archive_path)
    print(manifest_path)
    print(f"files={len(files)}")


if __name__ == "__main__":
    main()
