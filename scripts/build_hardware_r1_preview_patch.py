from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
BASE_SOURCE_COMMIT = "9203beda90135882181819312a72c3a6c7f092dd"
PATCH_FILES = [
    "services/hardware_case_markdown_agent.py",
    "services/hardware_case_r1_preview_store.py",
    "quality_knowledge/web/hardware_case_api.py",
    "quality_knowledge/web/p0_app.py",
    "quality_knowledge/web/static/hardware_case_word_import.js",
    "quality_knowledge/web/templates/hardware_case_word_import.html",
    "prompts/runtime/hardware_case/r1_extract_v2.md",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    source_commit = (
        os.getenv("HARDWARE_RELEASE_SOURCE_COMMIT")
        or os.getenv("GITHUB_SHA")
        or subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    )
    short = source_commit[:12]
    name = f"HARDWARE_R1_PREVIEW_PERSISTENCE_PERF_PATCH_{short}"
    stage = DIST / name
    archive = DIST / f"{name}.zip"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True, exist_ok=True)
    DIST.mkdir(parents=True, exist_ok=True)

    inventory = []
    for relative in PATCH_FILES:
        source = ROOT / relative
        if not source.is_file():
            raise SystemExit(f"MISSING_PATCH_FILE={relative}")
        target = stage / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        inventory.append(
            {
                "path": relative,
                "sha256": sha256(target),
                "size": target.stat().st_size,
            }
        )

    manifest = {
        "patch_id": name,
        "patch_type": "OVERLAY_PATCH",
        "product": "HARDWARE_CASE_R1",
        "base_source_commit": BASE_SOURCE_COMMIT,
        "source_commit": source_commit,
        "target": "R1_GOLDEN_KNOWLEDGE_V1.2",
        "changes": [
            "R1_PREVIEW_RESULT_PERSISTENCE",
            "R1_AGENT_PAYLOAD_COMPACTION",
        ],
        "formal_knowledge_write": False,
        "tree_mapping": False,
        "opensearch": False,
        "vector_search": False,
        "ocr_vision": False,
        "revision_management": False,
        "auto_publish": False,
        "apply_mode": "EXTRACT_INTO_EXISTING_FULL_PACKAGE_ROOT_AND_OVERWRITE",
        "files": inventory,
    }
    (stage / "PATCH_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (stage / "PATCH_README.txt").write_text(
        f"""HARDWARE R1 Preview Persistence + Payload Compaction Patch

BASE_SOURCE_COMMIT={BASE_SOURCE_COMMIT}
PATCH_SOURCE_COMMIT={source_commit}

Apply:
1. Stop the Hardware Case service.
2. Confirm your extracted full package is based on {BASE_SOURCE_COMMIT[:12]}.
3. Back up the package folder if desired.
4. Extract this ZIP directly INTO the existing full package root.
5. Allow overwrite of the listed files.
6. Start with the existing START_HARDWARE_CASE.bat / .command.
7. Open /p0/hardware-cases/word-import.

Expected:
- Existing/new Golden Preview results are stored in a separate local preview DB.
- Refreshing the page restores the latest Preview and shows recent history.
- No Formal Knowledge write is performed.
- Agent payload uses one Markdown string plus minimal evidence_blocks only.

Do not apply to an unrelated source baseline.
""",
        encoding="utf-8",
    )

    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for item in sorted(stage.rglob("*")):
            if item.is_file():
                bundle.write(item, item.relative_to(stage).as_posix())

    checksum = sha256(archive)
    sidecar = archive.with_suffix(".zip.sha256")
    sidecar.write_text(f"{checksum}  {archive.name}\n", encoding="utf-8")
    print(f"PATCH={archive}")
    print(f"SHA256={checksum}")
    print(f"SIZE={archive.stat().st_size}")
    print(f"BASE_SOURCE_COMMIT={BASE_SOURCE_COMMIT}")
    print(f"SOURCE_COMMIT={source_commit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
