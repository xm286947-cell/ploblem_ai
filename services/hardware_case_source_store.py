"""Company-local Evidence Source registry and resolver for Hardware Case.

The registry stores only safe metadata plus a path relative to a configured
source root. API callers never receive absolute server paths.
"""
from __future__ import annotations

import hashlib
import mimetypes
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from services.hardware_case_word import HardwareWordParseError, parse_docx


SCHEMA = """
CREATE TABLE IF NOT EXISTS hardware_case_source_registry (
    source_ref TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    mime_type TEXT,
    relative_path TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    source_status TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class HardwareCaseSourceError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_name(name: str) -> str:
    value = Path(str(name or "")).name.replace("\x00", "").strip()
    if not value or value in {".", ".."}:
        raise HardwareCaseSourceError("SOURCE_FILENAME_INVALID")
    return value


class HardwareCaseSourceStore:
    def __init__(
        self,
        db_path: str | Path,
        source_root: str | Path,
        *,
        max_upload_bytes: int = 100 * 1024 * 1024,
    ):
        self.db_path = Path(db_path)
        self.source_root = Path(source_root).resolve()
        self.max_upload_bytes = int(max_upload_bytes)
        self.source_root.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(SCHEMA)

    def _inside_root(self, relative_path: str) -> Path:
        candidate = (self.source_root / relative_path).resolve()
        try:
            candidate.relative_to(self.source_root)
        except ValueError as exc:
            raise HardwareCaseSourceError("SOURCE_PATH_OUTSIDE_ROOT") from exc
        return candidate

    @staticmethod
    def _public(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        return {
            "source_ref": row["source_ref"],
            "source_id": row["source_id"],
            "display_name": row["display_name"],
            "mime_type": row["mime_type"],
            "size_bytes": int(row["size_bytes"]),
            "sha256": row["sha256"],
            "source_status": row["source_status"],
            "created_at": row.get("created_at") if isinstance(row, dict) else row["created_at"],
            "updated_at": row.get("updated_at") if isinstance(row, dict) else row["updated_at"],
        }

    def _row(self, source_ref: str) -> sqlite3.Row | None:
        with self._connect() as connection:
            return connection.execute(
                "SELECT * FROM hardware_case_source_registry WHERE source_ref=?",
                (source_ref,),
            ).fetchone()

    def _set_status(self, source_ref: str, status: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE hardware_case_source_registry
                SET source_status=?, updated_at=CURRENT_TIMESTAMP
                WHERE source_ref=?
                """,
                (status, source_ref),
            )

    def register_file(
        self,
        source_ref: str,
        source_path: str | Path,
        *,
        mime_type: str | None = None,
    ) -> dict[str, Any]:
        ref = str(source_ref or "").strip()
        if not ref:
            raise HardwareCaseSourceError("SOURCE_REF_REQUIRED")
        source = Path(source_path)
        if not source.is_file():
            raise HardwareCaseSourceError("SOURCE_UNAVAILABLE")
        size = source.stat().st_size
        if size > self.max_upload_bytes:
            raise HardwareCaseSourceError("SOURCE_TOO_LARGE")

        digest = _sha256_file(source)
        display_name = _safe_name(source.name)
        source_id = digest
        relative = Path(digest[:2]) / digest / display_name
        target = self._inside_root(relative.as_posix())
        target.parent.mkdir(parents=True, exist_ok=True)

        existing = self._row(ref)
        if existing is not None and str(existing["sha256"]) != digest:
            raise HardwareCaseSourceError("SOURCE_REF_CONFLICT")

        if not target.exists():
            shutil.copy2(source, target)

        guessed = mimetypes.guess_type(display_name)[0]
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO hardware_case_source_registry(
                    source_ref,source_id,display_name,mime_type,relative_path,
                    size_bytes,sha256,source_status
                ) VALUES(?,?,?,?,?,?,?,'AVAILABLE')
                ON CONFLICT(source_ref) DO UPDATE SET
                    source_id=excluded.source_id,
                    display_name=excluded.display_name,
                    mime_type=excluded.mime_type,
                    relative_path=excluded.relative_path,
                    size_bytes=excluded.size_bytes,
                    sha256=excluded.sha256,
                    source_status='AVAILABLE',
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    ref,
                    source_id,
                    display_name,
                    mime_type or guessed or "application/octet-stream",
                    relative.as_posix(),
                    size,
                    digest,
                ),
            )
        return self.get_metadata(ref)

    def register_bytes(
        self,
        source_ref: str,
        filename: str,
        content: bytes,
        *,
        mime_type: str | None = None,
    ) -> dict[str, Any]:
        if len(content) > self.max_upload_bytes:
            raise HardwareCaseSourceError("SOURCE_TOO_LARGE")
        name = _safe_name(filename)
        digest = hashlib.sha256(content).hexdigest()
        temp = self.source_root / ".incoming" / digest / name
        temp.parent.mkdir(parents=True, exist_ok=True)
        temp.write_bytes(content)
        try:
            return self.register_file(source_ref, temp, mime_type=mime_type)
        finally:
            try:
                temp.unlink()
            except OSError:
                pass

    def get_metadata(self, source_ref: str) -> dict[str, Any]:
        row = self._row(str(source_ref or ""))
        if row is None:
            raise HardwareCaseSourceError("SOURCE_NOT_REGISTERED")
        meta = self._public(row)
        try:
            self.resolve_path(source_ref)
        except HardwareCaseSourceError as error:
            if error.code in {"SOURCE_UNAVAILABLE", "HASH_MISMATCH"}:
                meta["source_status"] = error.code
            else:
                raise
        return meta

    def resolve_path(self, source_ref: str) -> Path:
        row = self._row(str(source_ref or ""))
        if row is None:
            raise HardwareCaseSourceError("SOURCE_NOT_REGISTERED")
        path = self._inside_root(str(row["relative_path"]))
        if not path.is_file():
            self._set_status(source_ref, "SOURCE_UNAVAILABLE")
            raise HardwareCaseSourceError("SOURCE_UNAVAILABLE")
        digest = _sha256_file(path)
        if digest != str(row["sha256"]):
            self._set_status(source_ref, "HASH_MISMATCH")
            raise HardwareCaseSourceError("HASH_MISMATCH")
        if str(row["source_status"]) != "AVAILABLE":
            self._set_status(source_ref, "AVAILABLE")
        return path

    @staticmethod
    def _locator_matches(block: dict[str, Any], locator: dict[str, Any]) -> bool:
        current = block.get("source_locator") or {}
        if locator.get("block_id"):
            return current.get("block_id") == locator.get("block_id")
        for key in ("paragraph", "table", "image"):
            if key in locator:
                return current.get(key) == locator.get(key)
        return False

    def preview(
        self,
        source_ref: str,
        locator: dict[str, Any],
        *,
        context_blocks: int = 1,
    ) -> dict[str, Any]:
        path = self.resolve_path(source_ref)
        if path.suffix.lower() != ".docx":
            return {
                "source_ref": source_ref,
                "source_status": "AVAILABLE",
                "preview_status": "UNSUPPORTED_PREVIEW",
                "blocks": [],
            }
        try:
            parsed = parse_docx(path)
        except HardwareWordParseError as exc:
            return {
                "source_ref": source_ref,
                "source_status": "AVAILABLE",
                "preview_status": "PARSE_FAILED",
                "error": exc.code,
                "blocks": [],
            }

        index = None
        for i, block in enumerate(parsed.blocks):
            if self._locator_matches(block, locator):
                index = i
                break
        if index is None:
            raise HardwareCaseSourceError("SOURCE_LOCATOR_NOT_FOUND")

        span = max(int(context_blocks), 0)
        start = max(index - span, 0)
        end = min(index + span + 1, len(parsed.blocks))
        blocks = []
        for i in range(start, end):
            block = parsed.blocks[i]
            blocks.append(
                {
                    "block_id": block.get("block_id"),
                    "block_type": block.get("block_type"),
                    "text": block.get("text"),
                    "section_path": block.get("section_path") or [],
                    "source_locator": block.get("source_locator") or {},
                    "image_ref": block.get("image_ref"),
                    "matched": i == index,
                }
            )
        return {
            "source_ref": source_ref,
            "source_status": "AVAILABLE",
            "preview_status": "AVAILABLE",
            "blocks": blocks,
        }


__all__ = [
    "HardwareCaseSourceError",
    "HardwareCaseSourceStore",
]
