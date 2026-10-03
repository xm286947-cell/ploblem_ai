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
from services.hardware_migrations.v002_r1_source_binding import R1_SOURCE_BINDING_SCHEMA


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
    value = Path(str(name or "").replace("\\", "/")).name.replace("\x00", "").strip()
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
        initialize_schema: bool = True,
    ):
        self.db_path = Path(db_path)
        self.source_root = Path(source_root).resolve()
        self.max_upload_bytes = int(max_upload_bytes)
        self.source_root.mkdir(parents=True, exist_ok=True)
        if initialize_schema:
            self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(SCHEMA)
            connection.executescript(R1_SOURCE_BINDING_SCHEMA)

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
    def _case_id(value: str) -> str:
        case_id = str(value or "").strip()
        if not case_id:
            raise HardwareCaseSourceError("BUSINESS_CASE_ID_REQUIRED")
        return case_id

    def _binding_row(self, business_case_id: str) -> sqlite3.Row | None:
        case_id = self._case_id(business_case_id)
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT b.business_case_id,b.source_ref,b.source_id,b.source_status,
                       b.created_at,b.updated_at,
                       r.display_name,r.mime_type,r.size_bytes,r.sha256,
                       r.source_status AS registry_status
                FROM hardware_r1_source_binding b
                JOIN hardware_case_source_registry r
                  ON r.source_ref=b.source_ref
                WHERE b.business_case_id=?
                """,
                (case_id,),
            ).fetchone()

    def get_active_source(self, business_case_id: str) -> dict[str, Any]:
        row = self._binding_row(business_case_id)
        if row is None:
            raise HardwareCaseSourceError("SOURCE_NOT_REGISTERED")
        metadata = self.get_metadata(str(row["source_ref"]))
        return {
            "business_case_id": row["business_case_id"],
            "binding_status": row["source_status"],
            **metadata,
        }

    def register_active_bytes(
        self,
        business_case_id: str,
        filename: str,
        content: bytes,
        *,
        mime_type: str | None = None,
    ) -> dict[str, Any]:
        """Register exactly one ACTIVE source for a business case.

        The product contract intentionally has no source revision semantics.
        An existing ACTIVE binding is rejected even when the bytes are equal.
        """
        case_id = self._case_id(business_case_id)
        if self._binding_row(case_id) is not None:
            raise HardwareCaseSourceError("SOURCE_ALREADY_EXISTS")
        digest = hashlib.sha256(content).hexdigest()
        source_ref = f"r1:{case_id}:{digest}"
        created_registry = self._row(source_ref) is None
        metadata = self.register_bytes(
            source_ref,
            filename,
            content,
            mime_type=mime_type,
        )
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO hardware_r1_source_binding(
                        business_case_id,source_ref,source_id,source_status
                    ) VALUES(?,?,?,'ACTIVE')
                    """,
                    (case_id, source_ref, digest),
                )
        except sqlite3.IntegrityError as error:
            if created_registry:
                with self._connect() as connection:
                    connection.execute(
                        "DELETE FROM hardware_case_source_registry WHERE source_ref=?",
                        (source_ref,),
                    )
            raise HardwareCaseSourceError("SOURCE_ALREADY_EXISTS") from error
        return {
            "business_case_id": case_id,
            "binding_status": "ACTIVE",
            **metadata,
        }

    def add_formal_knowledge_reference(
        self,
        business_case_id: str,
        knowledge_id: str,
        *,
        source_id: str | None = None,
    ) -> dict[str, Any]:
        """Lock the ACTIVE source once Formal Knowledge references it."""
        case_id = self._case_id(business_case_id)
        knowledge = str(knowledge_id or "").strip()
        if not knowledge:
            raise HardwareCaseSourceError("KNOWLEDGE_ID_REQUIRED")
        binding = self._binding_row(case_id)
        if binding is None:
            raise HardwareCaseSourceError("SOURCE_NOT_REGISTERED")
        active_source_id = str(binding["source_id"])
        if source_id and str(source_id) != active_source_id:
            raise HardwareCaseSourceError("SOURCE_REFERENCE_MISMATCH")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO hardware_r1_source_formal_reference(
                    business_case_id,source_id,knowledge_id
                ) VALUES(?,?,?)
                ON CONFLICT(business_case_id,knowledge_id) DO NOTHING
                """,
                (case_id, active_source_id, knowledge),
            )
        return {
            "business_case_id": case_id,
            "source_id": active_source_id,
            "knowledge_id": knowledge,
            "reference_status": "ACTIVE",
        }

    def formal_knowledge_references(
        self,
        business_case_id: str,
    ) -> list[dict[str, Any]]:
        case_id = self._case_id(business_case_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT business_case_id,source_id,knowledge_id,created_at
                FROM hardware_r1_source_formal_reference
                WHERE business_case_id=?
                ORDER BY created_at,knowledge_id
                """,
                (case_id,),
            ).fetchall()
        return [
            {
                "business_case_id": row["business_case_id"],
                "source_id": row["source_id"],
                "knowledge_id": row["knowledge_id"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def delete_active_source(
        self,
        business_case_id: str,
        *,
        deleted_by: str | None = None,
    ) -> dict[str, Any]:
        """Delete a pre-publish ACTIVE source without introducing revisions."""
        case_id = self._case_id(business_case_id)
        binding = self._binding_row(case_id)
        if binding is None:
            raise HardwareCaseSourceError("SOURCE_NOT_REGISTERED")
        with self._connect() as connection:
            in_use = connection.execute(
                """
                SELECT COUNT(*)
                FROM hardware_r1_source_formal_reference
                WHERE business_case_id=? AND source_id=?
                """,
                (case_id, str(binding["source_id"])),
            ).fetchone()[0]
        if int(in_use or 0) > 0:
            raise HardwareCaseSourceError("SOURCE_IN_USE_BY_FORMAL_KNOWLEDGE")

        source_ref = str(binding["source_ref"])
        registry = self._row(source_ref)
        if registry is None:
            raise HardwareCaseSourceError("SOURCE_NOT_REGISTERED")
        path = self.resolve_path(source_ref)
        relative_path = str(registry["relative_path"])

        with self._connect() as connection:
            other_refs = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM hardware_case_source_registry
                    WHERE relative_path=? AND source_ref<>?
                    """,
                    (relative_path, source_ref),
                ).fetchone()[0]
                or 0
            )

        quarantine: Path | None = None
        if other_refs == 0:
            quarantine = path.with_name(
                "." + path.name + ".deleting-" + str(binding["source_id"])[:12]
            )
            if quarantine.exists():
                raise HardwareCaseSourceError("SOURCE_DELETE_FAILED")
            try:
                path.replace(quarantine)
            except OSError as error:
                raise HardwareCaseSourceError("SOURCE_DELETE_FAILED") from error

        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                locked = connection.execute(
                    """
                    SELECT source_ref,source_id FROM hardware_r1_source_binding
                    WHERE business_case_id=?
                    """,
                    (case_id,),
                ).fetchone()
                if locked is None:
                    raise HardwareCaseSourceError("SOURCE_NOT_REGISTERED")
                in_use = connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM hardware_r1_source_formal_reference
                    WHERE business_case_id=? AND source_id=?
                    """,
                    (case_id, str(locked["source_id"])),
                ).fetchone()[0]
                if int(in_use or 0) > 0:
                    raise HardwareCaseSourceError(
                        "SOURCE_IN_USE_BY_FORMAL_KNOWLEDGE"
                    )
                connection.execute(
                    """
                    INSERT INTO hardware_r1_source_delete_audit(
                        business_case_id,source_ref,source_id,display_name,
                        sha256,deleted_by
                    ) VALUES(?,?,?,?,?,?)
                    """,
                    (
                        case_id,
                        source_ref,
                        str(binding["source_id"]),
                        str(registry["display_name"]),
                        str(registry["sha256"]),
                        str(deleted_by or "").strip() or None,
                    ),
                )
                connection.execute(
                    "DELETE FROM hardware_r1_source_binding WHERE business_case_id=?",
                    (case_id,),
                )
                connection.execute(
                    "DELETE FROM hardware_case_source_registry WHERE source_ref=?",
                    (source_ref,),
                )
                if quarantine is not None:
                    quarantine.unlink()
                connection.commit()
        except Exception as error:
            if quarantine is not None and quarantine.exists():
                try:
                    quarantine.replace(path)
                except OSError:
                    pass
            if isinstance(error, HardwareCaseSourceError):
                raise
            raise HardwareCaseSourceError("SOURCE_DELETE_FAILED") from error

        return {
            "business_case_id": case_id,
            "source_ref": source_ref,
            "source_id": str(binding["source_id"]),
            "deleted": True,
            "formal_reference_count": 0,
            "bytes_deleted": other_refs == 0,
        }

    def list_delete_audit(
        self,
        *,
        business_case_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        query = """
            SELECT audit_id,business_case_id,source_ref,source_id,display_name,
                   sha256,deleted_by,deleted_at
            FROM hardware_r1_source_delete_audit
        """
        params: tuple[Any, ...] = ()
        if business_case_id:
            query += " WHERE business_case_id=?"
            params = (self._case_id(business_case_id),)
        query += " ORDER BY audit_id DESC LIMIT ?"
        params = (*params, limit)
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [
            {
                "audit_id": int(row["audit_id"]),
                "business_case_id": row["business_case_id"],
                "source_ref": row["source_ref"],
                "source_id": row["source_id"],
                "display_name": row["display_name"],
                "sha256": row["sha256"],
                "deleted_by": row["deleted_by"],
                "deleted_at": row["deleted_at"],
            }
            for row in rows
        ]

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
