"""Consistent backup and verified restore for the independent knowledge store."""
from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
import hashlib
import json
import shutil
import sqlite3
import tempfile

from .repository import MajorKnowledgeRepository


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_backup(repository: MajorKnowledgeRepository, output_zip: str | Path) -> dict:
    output = Path(output_zip)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="req022-backup-") as temporary:
        root = Path(temporary)
        database = root / "knowledge.sqlite3"
        source = repository.connect()
        target = sqlite3.connect(database)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        files = [path for path in repository.attachment_root.rglob("*") if path.is_file()]
        manifest = {
            "schema_version": repository.schema_version(),
            "database_sha256": _hash(database),
            "attachments": [
                {"path": path.relative_to(repository.attachment_root).as_posix(), "sha256": _hash(path), "size": path.stat().st_size}
                for path in sorted(files)
            ],
        }
        (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        with ZipFile(output, "w", ZIP_DEFLATED) as archive:
            archive.write(database, "knowledge.sqlite3")
            archive.write(root / "manifest.json", "manifest.json")
            for path in files:
                archive.write(path, f"attachments/{path.relative_to(repository.attachment_root).as_posix()}")
    return {"path": str(output), "sha256": _hash(output), "attachments": len(files), "schema_version": repository.schema_version()}


def restore_backup(backup_zip: str | Path, target_db: str | Path, target_attachments: str | Path) -> dict:
    backup = Path(backup_zip)
    db = Path(target_db)
    attachments = Path(target_attachments)
    if db.exists() or attachments.exists():
        raise FileExistsError("RESTORE_TARGET_MUST_NOT_EXIST")
    with tempfile.TemporaryDirectory(prefix="req022-restore-") as temporary:
        root = Path(temporary)
        with ZipFile(backup) as archive:
            for name in archive.namelist():
                candidate = (root / name).resolve()
                if root.resolve() not in candidate.parents:
                    raise ValueError("BACKUP_PATH_TRAVERSAL")
            archive.extractall(root)
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        if _hash(root / "knowledge.sqlite3") != manifest["database_sha256"]:
            raise ValueError("BACKUP_DATABASE_HASH_MISMATCH")
        for item in manifest["attachments"]:
            path = root / "attachments" / item["path"]
            if not path.exists() or _hash(path) != item["sha256"]:
                raise ValueError(f"BACKUP_ATTACHMENT_HASH_MISMATCH:{item['path']}")
        db.parent.mkdir(parents=True, exist_ok=True)
        attachments.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / "knowledge.sqlite3", db)
        extracted_attachments = root / "attachments"
        if extracted_attachments.exists():
            shutil.copytree(extracted_attachments, attachments)
        else:
            attachments.mkdir()
    restored = MajorKnowledgeRepository(db, attachments)
    return {"database": str(db), "attachments": str(attachments), "schema_version": restored.schema_version(), "attachment_count": len(manifest["attachments"])}
