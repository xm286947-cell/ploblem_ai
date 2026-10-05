"""Company-local Evidence Source registry and resolver for Hardware Case.

The registry stores only safe metadata plus a path relative to a configured
source root. API callers never receive absolute server paths.
"""
from __future__ import annotations

import hashlib
import mimetypes
import os
import shutil
import sqlite3
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
from uuid import uuid4

from services.hardware_asset_operation_journal import (
    HardwareAssetOperationJournal,
    HardwareAssetOperationJournalError,
)
from services.hardware_asset_repository import (
    CandidateAssetRepository,
    CandidateAssetRepositoryError,
)
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
        operation_journal: HardwareAssetOperationJournal | None = None,
        candidate_repository: CandidateAssetRepository | None = None,
    ):
        self.db_path = Path(db_path)
        self.source_root = Path(source_root).resolve()
        self.max_upload_bytes = int(max_upload_bytes)
        asset_db_path = self.db_path.with_name("hardware_asset.db")
        self.operation_journal = operation_journal or (
            HardwareAssetOperationJournal(asset_db_path) if asset_db_path.is_file() else None
        )
        self.candidate_repository = candidate_repository or (
            CandidateAssetRepository(asset_db_path) if asset_db_path.is_file() else None
        )
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

    def _journal_path(self, relative_path: Any) -> Path:
        raw = str(relative_path or "")
        relative = PurePosixPath(raw)
        if (
            not raw
            or "\\" in raw
            or relative.is_absolute()
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            raise HardwareCaseSourceError("SOURCE_OPERATION_JOURNAL_INVALID")
        return self._inside_root(Path(*relative.parts).as_posix())

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

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        try:
            descriptor = os.open(path, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError:
            # Directory fsync is unsupported on some Windows/filesystem pairs.
            pass

    def _require_operation_journal(self) -> HardwareAssetOperationJournal:
        if self.operation_journal is None:
            raise HardwareCaseSourceError("SOURCE_OPERATION_JOURNAL_UNAVAILABLE")
        return self.operation_journal

    def _copy_to_incoming(self, source: Path, operation_id: str, display_name: str) -> Path:
        incoming = self.source_root / ".incoming" / operation_id / display_name
        try:
            incoming.parent.mkdir(parents=True, exist_ok=False)
            self._fsync_directory(incoming.parent.parent)
            with source.open("rb") as source_stream, incoming.open("xb") as target_stream:
                shutil.copyfileobj(source_stream, target_stream, length=1024 * 1024)
                target_stream.flush()
                os.fsync(target_stream.fileno())
            self._fsync_directory(incoming.parent)
            return incoming
        except OSError as error:
            raise HardwareCaseSourceError("SOURCE_UPLOAD_STAGING_FAILED") from error

    def _write_bytes_to_incoming(
        self, content: bytes, operation_id: str, display_name: str
    ) -> Path:
        incoming = self.source_root / ".incoming" / operation_id / display_name
        try:
            incoming.parent.mkdir(parents=True, exist_ok=False)
            self._fsync_directory(incoming.parent.parent)
            with incoming.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            self._fsync_directory(incoming.parent)
            return incoming
        except OSError as error:
            raise HardwareCaseSourceError("SOURCE_UPLOAD_STAGING_FAILED") from error

    def _quarantine_path(self, operation_id: str, display_name: str) -> Path:
        return self.source_root / ".quarantine" / operation_id / display_name

    def _quarantine(self, path: Path, operation_id: str, display_name: str) -> Path:
        target = self._quarantine_path(operation_id, display_name)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise HardwareCaseSourceError("SOURCE_OPERATION_QUARANTINE_CONFLICT")
        if path.exists():
            os.replace(path, target)
            self._fsync_directory(path.parent)
            self._fsync_directory(target.parent)
            self._fsync_directory(target.parent.parent)
        return target

    def _commit_upload(
        self,
        *,
        source_ref: str,
        display_name: str,
        staged_path: Path,
        mime_type: str | None,
        business_case_id: str | None,
        source_id: str,
    ) -> dict[str, Any]:
        journal = self._require_operation_journal()
        digest = _sha256_file(staged_path)
        size = staged_path.stat().st_size
        if size > self.max_upload_bytes:
            raise HardwareCaseSourceError("SOURCE_TOO_LARGE")
        if digest != source_id:
            raise HardwareCaseSourceError("SOURCE_HASH_MISMATCH")
        relative = Path(digest[:2]) / digest / display_name
        target = self._inside_root(relative.as_posix())
        guessed = mimetypes.guess_type(display_name)[0]
        chosen_mime = mime_type or guessed or "application/octet-stream"
        operation_id = staged_path.parent.name
        fingerprint = {
            "business_case_id": business_case_id,
            "display_name": display_name,
            "mime_type": chosen_mime,
            "relative_path": relative.as_posix(),
            "size_bytes": size,
            "source_id": source_id,
            "source_ref": source_ref,
            "staged_relative_path": staged_path.relative_to(self.source_root).as_posix(),
        }
        try:
            if business_case_id is not None and journal.has_nonterminal_asset_operation(
                business_case_id=business_case_id,
                excluding_operation_id=operation_id,
            ):
                raise HardwareCaseSourceError("SOURCE_RECOVERY_REQUIRED")
            journal.prepare(
                operation_id=operation_id,
                operation_type="SOURCE_UPLOAD",
                business_case_id=business_case_id,
                candidate_id=None,
                source_id=source_id,
                desired_action="REGISTER_ACTIVE" if business_case_id else "REGISTER_SOURCE",
                request_fingerprint=fingerprint,
            )
            existing = self._row(source_ref)
            if existing is not None and str(existing["sha256"]) != digest:
                journal.transition(
                    operation_id,
                    "FAILED",
                    error_code="SOURCE_REF_CONFLICT",
                    recovery_action="NO_SOURCE_BYTES_COMMITTED",
                )
                raise HardwareCaseSourceError("SOURCE_REF_CONFLICT")

            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if not target.is_file() or _sha256_file(target) != digest:
                    self._quarantine(staged_path, operation_id, display_name)
                    journal.transition(
                        operation_id,
                        "OUTCOME_UNKNOWN",
                        error_code="SOURCE_CONTENT_ADDRESS_MISMATCH",
                        recovery_action="QUARANTINED_STAGED_BYTES",
                    )
                    raise HardwareCaseSourceError("SOURCE_RECOVERY_REQUIRED")
                staged_path.unlink(missing_ok=True)
            else:
                os.replace(staged_path, target)
                self._fsync_directory(staged_path.parent)
                self._fsync_directory(target.parent)

            try:
                with self._connect() as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    registry = connection.execute(
                        "SELECT source_id,sha256 FROM hardware_case_source_registry WHERE source_ref=?",
                        (source_ref,),
                    ).fetchone()
                    if registry is not None and (
                        str(registry["source_id"]) != source_id
                        or str(registry["sha256"]) != digest
                    ):
                        raise HardwareCaseSourceError("SOURCE_REF_CONFLICT")
                    if business_case_id is not None:
                        binding = connection.execute(
                            "SELECT source_ref,source_id FROM hardware_r1_source_binding WHERE business_case_id=?",
                            (business_case_id,),
                        ).fetchone()
                        if binding is not None and (
                            str(binding["source_ref"]) != source_ref
                            or str(binding["source_id"]) != source_id
                        ):
                            raise HardwareCaseSourceError("SOURCE_ALREADY_EXISTS")
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
                            source_ref,
                            source_id,
                            display_name,
                            chosen_mime,
                            relative.as_posix(),
                            size,
                            digest,
                        ),
                    )
                    if business_case_id is not None and connection.execute(
                        "SELECT 1 FROM hardware_r1_source_binding WHERE business_case_id=?",
                        (business_case_id,),
                    ).fetchone() is None:
                        connection.execute(
                            """
                            INSERT INTO hardware_r1_source_binding(
                                business_case_id,source_ref,source_id,source_status
                            ) VALUES(?,?,?,'ACTIVE')
                            """,
                            (business_case_id, source_ref, source_id),
                        )
                    connection.commit()
            except HardwareCaseSourceError:
                raise
            except sqlite3.IntegrityError as error:
                raise HardwareCaseSourceError("SOURCE_ALREADY_EXISTS") from error
            except sqlite3.Error as error:
                raise HardwareCaseSourceError("SOURCE_UPLOAD_COMMIT_FAILED") from error

            journal.transition(operation_id, "LOCAL_COMMITTED")
            journal.transition(operation_id, "COMPLETED", recovery_action="UPLOAD_VERIFIED")
            shutil.rmtree(staged_path.parent, ignore_errors=True)
            self._fsync_directory(staged_path.parent.parent)
            return {
                "source_ref": source_ref,
                "source_id": source_id,
                "display_name": display_name,
                "mime_type": chosen_mime,
                "relative_path": relative.as_posix(),
                "size_bytes": size,
                "sha256": digest,
                "source_status": "AVAILABLE",
                "created_at": None,
                "updated_at": None,
            }
        except HardwareAssetOperationJournalError as error:
            raise HardwareCaseSourceError(error.code) from error

    def _stage_and_register(
        self,
        *,
        source_ref: str,
        display_name: str,
        mime_type: str | None,
        business_case_id: str | None,
        source_id: str,
        source_file: Path | None = None,
        content: bytes | None = None,
    ) -> dict[str, Any]:
        ref = str(source_ref or "").strip()
        if not ref:
            raise HardwareCaseSourceError("SOURCE_REF_REQUIRED")
        operation_id = "HOP-" + uuid4().hex
        staged_path = (
            self._copy_to_incoming(source_file, operation_id, display_name)
            if source_file is not None
            else self._write_bytes_to_incoming(content or b"", operation_id, display_name)
        )
        try:
            meta = self._commit_upload(
                source_ref=ref,
                display_name=display_name,
                staged_path=staged_path,
                mime_type=mime_type,
                business_case_id=business_case_id,
                source_id=source_id,
            )
            return meta
        except Exception:
            # Keep operation-owned staging for nonterminal journal recovery.
            if self.operation_journal is None:
                shutil.rmtree(staged_path.parent, ignore_errors=True)
                self._fsync_directory(staged_path.parent.parent)
            else:
                try:
                    operation = self.operation_journal.get(operation_id)
                except HardwareAssetOperationJournalError:
                    operation = None
                if operation is None or operation["operation_state"] in {"FAILED", "CANCELLED", "COMPLETED"}:
                    shutil.rmtree(staged_path.parent, ignore_errors=True)
                    self._fsync_directory(staged_path.parent.parent)
            raise

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
        self._stage_and_register(
            source_ref=ref,
            display_name=display_name,
            mime_type=mime_type,
            business_case_id=None,
            source_id=digest,
            source_file=source,
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
        self._stage_and_register(
            source_ref=source_ref,
            display_name=name,
            mime_type=mime_type,
            business_case_id=None,
            source_id=digest,
            content=content,
        )
        return self.get_metadata(source_ref)

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
        if len(content) > self.max_upload_bytes:
            raise HardwareCaseSourceError("SOURCE_TOO_LARGE")
        digest = hashlib.sha256(content).hexdigest()
        existing = self._binding_row(case_id)
        if existing is not None:
            current = self.get_active_source(case_id)
            if (
                str(current.get("source_id") or "") == digest
                and str(current.get("sha256") or "") == digest
                and int(current.get("size_bytes") or -1) == len(content)
                and str(current.get("binding_status") or "").upper() == "ACTIVE"
                and str(current.get("source_status") or "").upper() == "AVAILABLE"
            ):
                return {
                    **current,
                    "business_case_id": case_id,
                    "binding_status": "ACTIVE",
                    "idempotent_reuse": True,
                }
            raise HardwareCaseSourceError("SOURCE_ALREADY_EXISTS")
        source_ref = f"r1:{case_id}:{digest}"
        name = _safe_name(filename)
        metadata = self._stage_and_register(
            source_ref=source_ref,
            display_name=name,
            mime_type=mime_type,
            business_case_id=case_id,
            source_id=digest,
            content=content,
        )
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
        journal = self._require_operation_journal()
        candidate_repository = self.candidate_repository
        if candidate_repository is None:
            raise HardwareCaseSourceError("SOURCE_CANDIDATE_STORE_UNAVAILABLE")
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
        source_id = str(binding["source_id"])

        try:
            candidates = candidate_repository.find_by_business_case_id(case_id)
            source_candidates = [item for item in candidates if item["source_id"] == source_id]
            if any(item["promotion_status"] != "NOT_STARTED" for item in source_candidates):
                raise HardwareCaseSourceError("SOURCE_DELETE_BLOCKED_BY_PROMOTION")
            if journal.has_nonterminal_asset_operation(
                business_case_id=case_id,
            ):
                raise HardwareCaseSourceError("SOURCE_RECOVERY_LOCKED")
        except (CandidateAssetRepositoryError, HardwareAssetOperationJournalError) as error:
            code = getattr(error, "code", "SOURCE_RECOVERY_LOCKED")
            if code in {"CANDIDATE_ASSET_STORE_UNAVAILABLE", "CANDIDATE_DATA_INTEGRITY_ERROR"}:
                code = "SOURCE_CANDIDATE_STORE_UNAVAILABLE"
            if code == "OPERATION_JOURNAL_UNAVAILABLE":
                code = "SOURCE_OPERATION_JOURNAL_UNAVAILABLE"
            raise HardwareCaseSourceError(code) from error

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
        operation_id = "HOP-" + uuid4().hex
        quarantine_relative = (
            (Path(".quarantine") / operation_id / path.name).as_posix()
            if other_refs == 0
            else None
        )
        quarantine = self._inside_root(quarantine_relative) if quarantine_relative else None
        fingerprint = {
            "business_case_id": case_id,
            "display_name": str(registry["display_name"]),
            "other_source_ref_count": other_refs,
            "quarantine_relative_path": quarantine_relative,
            "relative_path": relative_path,
            "sha256": str(registry["sha256"]),
            "size_bytes": int(registry["size_bytes"]),
            "source_id": source_id,
            "source_ref": source_ref,
        }
        logical_committed = False
        try:
            journal.prepare(
                operation_id=operation_id,
                operation_type="SOURCE_DELETE",
                business_case_id=case_id,
                candidate_id=(source_candidates[0]["candidate_id"] if source_candidates else None),
                source_id=source_id,
                desired_action="DELETE_ACTIVE_SOURCE",
                request_fingerprint=fingerprint,
            )
            if quarantine is not None:
                quarantine.parent.mkdir(parents=True, exist_ok=False)
                os.replace(path, quarantine)
                self._fsync_directory(path.parent)
                self._fsync_directory(quarantine.parent)
                journal.transition(
                    operation_id,
                    "PREPARED",
                    recovery_action="SOURCE_BYTES_QUARANTINED",
                )

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
                if (
                    locked["source_ref"] != source_ref
                    or locked["source_id"] != source_id
                ):
                    raise HardwareCaseSourceError("SOURCE_DELETE_CONFLICT")
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
                connection.commit()
                logical_committed = True

            try:
                candidate_repository.invalidate_for_source(
                    case_id,
                    source_id,
                    source_ref=source_ref,
                    actor=str(deleted_by or "HARDWARE_SOURCE_DELETE"),
                    reason="Source deleted before formal publication",
                )
            except CandidateAssetRepositoryError as error:
                code = (
                    "SOURCE_DELETE_BLOCKED_BY_PROMOTION"
                    if error.code == "CANDIDATE_LOCKED_BY_PROMOTION"
                    else "SOURCE_CANDIDATE_STORE_UNAVAILABLE"
                    if error.code in {"CANDIDATE_ASSET_STORE_UNAVAILABLE", "CANDIDATE_DATA_INTEGRITY_ERROR"}
                    else "SOURCE_CANDIDATE_INVALIDATION_FAILED"
                )
                raise HardwareCaseSourceError(code) from error

            journal.transition(
                operation_id,
                "LOCAL_COMMITTED",
                recovery_action="SOURCE_AND_CANDIDATE_INVALIDATED",
            )
            bytes_deleted = False
            cleanup_pending = False
            if quarantine is not None:
                try:
                    quarantine.unlink(missing_ok=True)
                    self._fsync_directory(quarantine.parent)
                    try:
                        quarantine.parent.rmdir()
                        self._fsync_directory(quarantine.parent.parent)
                    except OSError:
                        pass
                    bytes_deleted = True
                except OSError:
                    cleanup_pending = True
                    journal.transition(
                        operation_id,
                        "LOCAL_COMMITTED",
                        recovery_action="QUARANTINE_DELETE_PENDING",
                    )
            if not cleanup_pending:
                journal.transition(
                    operation_id,
                    "COMPLETED",
                    recovery_action="SOURCE_DELETE_RECOVERED",
                )
        except Exception as error:
            if not logical_committed and quarantine is not None and quarantine.exists():
                try:
                    if not path.exists():
                        path.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(quarantine, path)
                        self._fsync_directory(quarantine.parent)
                        self._fsync_directory(path.parent)
                    journal.transition(
                        operation_id,
                        "CANCELLED",
                        error_code=str(getattr(error, "code", "SOURCE_DELETE_FAILED")),
                        recovery_action="QUARANTINE_RESTORED",
                    )
                except OSError:
                    pass
            if isinstance(error, HardwareCaseSourceError):
                raise
            if isinstance(error, HardwareAssetOperationJournalError):
                raise HardwareCaseSourceError(error.code) from error
            raise HardwareCaseSourceError("SOURCE_DELETE_FAILED") from error

        return {
            "business_case_id": case_id,
            "source_ref": source_ref,
            "source_id": source_id,
            "deleted": True,
            "formal_reference_count": 0,
            "bytes_deleted": bytes_deleted,
            "cleanup_pending": cleanup_pending,
        }

    def recover_source_operations(self) -> list[dict[str, Any]]:
        """Deterministically reconcile nonterminal local Source operations."""
        journal = self._require_operation_journal()
        recovered: list[dict[str, Any]] = []
        try:
            for operation in journal.list_nonterminal():
                operation_type = operation["operation_type"]
                try:
                    if operation_type == "SOURCE_UPLOAD":
                        recovered.append(self._recover_source_upload(operation))
                    elif operation_type == "SOURCE_DELETE":
                        recovered.append(self._recover_source_delete(operation))
                except HardwareCaseSourceError as error:
                    if error.code not in {
                        "SOURCE_UPLOAD_RECONCILIATION_CONFLICT",
                        "SOURCE_UPLOAD_RECOVERY_REQUIRED",
                        "SOURCE_DELETE_RECOVERY_CONFLICT",
                        "SOURCE_DELETE_RECOVERY_REQUIRED",
                    }:
                        raise
                    recovered.append(
                        {
                            "operation_id": operation["operation_id"],
                            "operation_state": "ASSET_SCOPED_BLOCKED",
                            "error_code": error.code,
                        }
                    )
        except HardwareAssetOperationJournalError as error:
            raise HardwareCaseSourceError(error.code) from error
        except (OSError, sqlite3.Error) as error:
            raise HardwareCaseSourceError("SOURCE_RECOVERY_REQUIRED") from error
        return recovered

    def _recover_source_upload(self, operation: Mapping[str, Any]) -> dict[str, Any]:
        journal = self._require_operation_journal()
        fingerprint = operation.get("request_fingerprint")
        if not isinstance(fingerprint, dict):
            raise HardwareCaseSourceError("SOURCE_OPERATION_JOURNAL_INVALID")
        try:
            operation_id = str(operation["operation_id"])
            source_ref = str(fingerprint["source_ref"])
            source_id = str(fingerprint["source_id"]).lower()
            relative = str(fingerprint["relative_path"])
            display_name = _safe_name(str(fingerprint["display_name"]))
            size_bytes = int(fingerprint["size_bytes"])
            staged = self._journal_path(fingerprint["staged_relative_path"])
            target = self._journal_path(relative)
            case_id = str(fingerprint["business_case_id"]) if fingerprint.get("business_case_id") else None
        except (KeyError, TypeError, ValueError) as error:
            raise HardwareCaseSourceError("SOURCE_OPERATION_JOURNAL_INVALID") from error
        if len(source_id) != 64 or any(ch not in "0123456789abcdef" for ch in source_id):
            raise HardwareCaseSourceError("SOURCE_OPERATION_JOURNAL_INVALID")
        expected_relative = (Path(source_id[:2]) / source_id / display_name).as_posix()
        expected_staged = (Path(".incoming") / operation_id / display_name).as_posix()
        if relative != expected_relative or staged.relative_to(self.source_root).as_posix() != expected_staged:
            raise HardwareCaseSourceError("SOURCE_OPERATION_JOURNAL_INVALID")
        if case_id is not None and source_ref != f"r1:{case_id}:{source_id}":
            raise HardwareCaseSourceError("SOURCE_OPERATION_JOURNAL_INVALID")

        registry_before = self._row(source_ref)
        if registry_before is not None and (
            str(registry_before["source_id"]) != source_id
            or str(registry_before["sha256"]) != source_id
        ):
            self._quarantine_upload_artifacts(staged, target, operation_id, display_name)
            journal.transition(
                operation_id,
                "OUTCOME_UNKNOWN",
                error_code="SOURCE_REF_CONFLICT",
                recovery_action="SOURCE_REF_CONFLICT_QUARANTINED",
            )
            raise HardwareCaseSourceError("SOURCE_UPLOAD_RECONCILIATION_CONFLICT")

        active = self._binding_row(case_id) if case_id else None
        if active is not None and (
            str(active["source_ref"]) != source_ref or str(active["source_id"]) != source_id
        ):
            self._quarantine_upload_artifacts(staged, target, operation_id, display_name)
            journal.transition(
                operation_id,
                "OUTCOME_UNKNOWN",
                error_code="SOURCE_UPLOAD_RECONCILIATION_CONFLICT",
                recovery_action="UNBOUND_BYTES_QUARANTINED",
            )
            raise HardwareCaseSourceError("SOURCE_UPLOAD_RECONCILIATION_CONFLICT")

        if target.is_file() and _sha256_file(target) == source_id and target.stat().st_size == size_bytes:
            staged.unlink(missing_ok=True)
        elif staged.is_file() and _sha256_file(staged) == source_id and staged.stat().st_size == size_bytes:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                self._quarantine_upload_artifacts(staged, target, operation_id, display_name)
                journal.transition(
                    operation_id,
                    "OUTCOME_UNKNOWN",
                    error_code="SOURCE_UPLOAD_RECONCILIATION_CONFLICT",
                    recovery_action="CONTENT_ADDRESS_CONFLICT_QUARANTINED",
                )
                raise HardwareCaseSourceError("SOURCE_UPLOAD_RECONCILIATION_CONFLICT")
            os.replace(staged, target)
            self._fsync_directory(staged.parent)
            self._fsync_directory(target.parent)
        else:
            self._quarantine_upload_artifacts(staged, target, operation_id, display_name)
            journal.transition(
                operation_id,
                "OUTCOME_UNKNOWN",
                error_code="SOURCE_UPLOAD_BYTES_UNAVAILABLE",
                recovery_action="UPLOAD_BYTES_QUARANTINED",
            )
            raise HardwareCaseSourceError("SOURCE_UPLOAD_RECOVERY_REQUIRED")

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT source_ref,source_id FROM hardware_r1_source_binding WHERE business_case_id=?",
                (case_id,),
            ).fetchone() if case_id else None
            if current is not None and (
                str(current["source_ref"]) != source_ref
                or str(current["source_id"]) != source_id
            ):
                connection.rollback()
                self._quarantine_upload_artifacts(staged, target, operation_id, display_name)
                journal.transition(
                    operation_id,
                    "OUTCOME_UNKNOWN",
                    error_code="SOURCE_UPLOAD_RECONCILIATION_CONFLICT",
                    recovery_action="UNBOUND_BYTES_QUARANTINED",
                )
                raise HardwareCaseSourceError("SOURCE_UPLOAD_RECONCILIATION_CONFLICT")
            existing_registry = connection.execute(
                "SELECT source_id,sha256 FROM hardware_case_source_registry WHERE source_ref=?",
                (source_ref,),
            ).fetchone()
            if existing_registry is not None and (
                str(existing_registry["source_id"]) != source_id
                or str(existing_registry["sha256"]) != source_id
            ):
                connection.rollback()
                self._quarantine_upload_artifacts(staged, target, operation_id, display_name)
                journal.transition(
                    operation_id,
                    "OUTCOME_UNKNOWN",
                    error_code="SOURCE_REF_CONFLICT",
                    recovery_action="SOURCE_REF_CONFLICT_QUARANTINED",
                )
                raise HardwareCaseSourceError("SOURCE_UPLOAD_RECONCILIATION_CONFLICT")
            mime_type = str(fingerprint.get("mime_type") or "application/octet-stream")
            connection.execute(
                """
                INSERT INTO hardware_case_source_registry(
                    source_ref,source_id,display_name,mime_type,relative_path,
                    size_bytes,sha256,source_status
                ) VALUES(?,?,?,?,?,?,?,'AVAILABLE')
                ON CONFLICT(source_ref) DO UPDATE SET
                    source_id=excluded.source_id,display_name=excluded.display_name,
                    mime_type=excluded.mime_type,relative_path=excluded.relative_path,
                    size_bytes=excluded.size_bytes,sha256=excluded.sha256,
                    source_status='AVAILABLE',updated_at=CURRENT_TIMESTAMP
                """,
                (source_ref, source_id, display_name, mime_type, relative, size_bytes, source_id),
            )
            if case_id is not None and current is None:
                connection.execute(
                    """
                    INSERT INTO hardware_r1_source_binding(
                        business_case_id,source_ref,source_id,source_status
                    ) VALUES(?,?,?,'ACTIVE')
                    """,
                    (case_id, source_ref, source_id),
                )
            connection.commit()
        journal.transition(
            operation_id,
            "LOCAL_COMMITTED",
            recovery_action="SOURCE_REGISTRY_AND_BINDING_VERIFIED",
        )
        shutil.rmtree(staged.parent, ignore_errors=True)
        self._fsync_directory(staged.parent.parent)
        result = journal.transition(
            operation_id,
            "COMPLETED",
            recovery_action="SOURCE_UPLOAD_RECOVERED",
        )
        return {"operation_id": operation_id, "operation_state": result["operation_state"]}

    def _quarantine_upload_artifacts(
        self, staged: Path, target: Path, operation_id: str, display_name: str
    ) -> None:
        if staged.exists():
            self._quarantine(staged, operation_id, "staged-" + display_name)
        # Move final bytes only when no committed registry refers to them.
        with self._connect() as connection:
            references = int(
                connection.execute(
                    "SELECT COUNT(*) FROM hardware_case_source_registry WHERE relative_path=?",
                    (target.relative_to(self.source_root).as_posix(),),
                ).fetchone()[0]
                or 0
            )
        if references == 0 and target.exists():
            self._quarantine(target, operation_id, "final-" + display_name)

    def _recover_source_delete(self, operation: Mapping[str, Any]) -> dict[str, Any]:
        journal = self._require_operation_journal()
        candidate_repository = self.candidate_repository
        if candidate_repository is None:
            raise HardwareCaseSourceError("SOURCE_CANDIDATE_STORE_UNAVAILABLE")
        fingerprint = operation.get("request_fingerprint")
        if not isinstance(fingerprint, dict):
            raise HardwareCaseSourceError("SOURCE_OPERATION_JOURNAL_INVALID")
        try:
            operation_id = str(operation["operation_id"])
            case_id = self._case_id(str(fingerprint["business_case_id"]))
            source_ref = str(fingerprint["source_ref"])
            source_id = str(fingerprint["source_id"]).lower()
            original = self._journal_path(fingerprint["relative_path"])
            quarantine_relative = fingerprint.get("quarantine_relative_path")
            quarantine = self._journal_path(quarantine_relative) if quarantine_relative else None
            expected_hash = str(fingerprint["sha256"]).lower()
            expected_size = int(fingerprint["size_bytes"])
            other_refs = int(fingerprint.get("other_source_ref_count") or 0)
        except (KeyError, TypeError, ValueError) as error:
            raise HardwareCaseSourceError("SOURCE_OPERATION_JOURNAL_INVALID") from error

        binding = self._binding_row(case_id)
        if binding is not None:
            if str(binding["source_ref"]) != source_ref or str(binding["source_id"]) != source_id:
                journal.transition(
                    operation_id,
                    "OUTCOME_UNKNOWN",
                    error_code="SOURCE_DELETE_RECONCILIATION_CONFLICT",
                    recovery_action="ACTIVE_SOURCE_CHANGED",
                )
                raise HardwareCaseSourceError("SOURCE_DELETE_RECOVERY_CONFLICT")
            if quarantine is not None and quarantine.is_file():
                if not quarantine.is_file() or _sha256_file(quarantine) != expected_hash:
                    raise HardwareCaseSourceError("SOURCE_DELETE_RECOVERY_REQUIRED")
                if original.exists():
                    if _sha256_file(original) != expected_hash:
                        raise HardwareCaseSourceError("SOURCE_DELETE_RECOVERY_REQUIRED")
                    quarantine.unlink()
                else:
                    original.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(quarantine, original)
                    self._fsync_directory(quarantine.parent)
                    self._fsync_directory(original.parent)
            elif not original.is_file() or _sha256_file(original) != expected_hash:
                raise HardwareCaseSourceError("SOURCE_DELETE_RECOVERY_REQUIRED")
            result = journal.transition(
                operation_id,
                "CANCELLED",
                recovery_action="QUARANTINE_RESTORED_SOURCE_ACTIVE",
            )
            return {"operation_id": operation_id, "operation_state": result["operation_state"]}

        with self._connect() as connection:
            references = int(
                connection.execute(
                    "SELECT COUNT(*) FROM hardware_r1_source_formal_reference WHERE business_case_id=? AND source_id=?",
                    (case_id, source_id),
                ).fetchone()[0]
                or 0
            )
            other_registry_refs = int(
                connection.execute(
                    "SELECT COUNT(*) FROM hardware_case_source_registry WHERE relative_path=? AND source_ref<>?",
                    (str(fingerprint["relative_path"]), source_ref),
                ).fetchone()[0]
                or 0
            )
        if references:
            journal.transition(
                operation_id,
                "OUTCOME_UNKNOWN",
                error_code="SOURCE_IN_USE_BY_FORMAL_KNOWLEDGE",
                recovery_action="DELETE_LOGICAL_STATE_CONFLICT",
            )
            raise HardwareCaseSourceError("SOURCE_DELETE_RECOVERY_CONFLICT")
        try:
            candidate_repository.invalidate_for_source(
                case_id,
                source_id,
                source_ref=source_ref,
                actor="HARDWARE_SOURCE_DELETE_RECOVERY",
                reason="Recovered pre-publish Source deletion",
            )
        except CandidateAssetRepositoryError as error:
            raise HardwareCaseSourceError("SOURCE_DELETE_RECOVERY_REQUIRED") from error
        journal.transition(
            operation_id,
            "LOCAL_COMMITTED",
            recovery_action="SOURCE_AND_CANDIDATE_INVALIDATED",
        )

        cleanup_pending = False
        if other_refs == 0 and other_registry_refs == 0:
            cleanup = quarantine if quarantine is not None and quarantine.exists() else original
            if cleanup.exists():
                if not cleanup.is_file() or cleanup.stat().st_size != expected_size or _sha256_file(cleanup) != expected_hash:
                    raise HardwareCaseSourceError("SOURCE_DELETE_RECOVERY_REQUIRED")
                if quarantine is None or cleanup == original:
                    quarantine = self._quarantine(cleanup, operation_id, cleanup.name)
                try:
                    quarantine.unlink(missing_ok=True)
                    self._fsync_directory(quarantine.parent)
                except OSError:
                    cleanup_pending = True
        if cleanup_pending:
            result = journal.transition(
                operation_id,
                "LOCAL_COMMITTED",
                recovery_action="QUARANTINE_DELETE_PENDING",
            )
        else:
            result = journal.transition(
                operation_id,
                "COMPLETED",
                recovery_action="SOURCE_DELETE_RECOVERED",
            )
        return {"operation_id": operation_id, "operation_state": result["operation_state"]}

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
