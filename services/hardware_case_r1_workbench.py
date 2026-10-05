"""Hardware R1 Batch-first Knowledge Production Workbench.

This layer is orchestration only. It reuses the frozen R1 parser, Stage A/B
pipeline, validation/cache semantics and Golden Knowledge Object. It never
publishes Formal Knowledge and never becomes a second Knowledge store.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable
from uuid import uuid4

from services.hardware_asset_repository import CandidateAssetRepositoryError
from services.hardware_case_markdown_agent import (
    build_markdown_view,
    run_r1_agent_extraction,
)
from services.hardware_case_word import parse_docx
from services.hardware_case_r1_runtime import (
    R1_STAGE_A_VALIDATOR_VERSION,
    R1_STAGE_B_VALIDATOR_VERSION,
)


WORKBENCH_CONTRACT_VERSION = "hardware-r1-knowledge-production-workbench/v1"

FINAL_RESULTS = {"CANDIDATE_READY", "REVIEW", "FAILED"}
FAILED_RESULTS = {"PARSE_FAILED", "STAGE_A_FAILED", "STAGE_B_FAILED", "GATE_FAILED"}


class HardwareR1WorkbenchError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stage_status(trace: dict[str, Any], stage: str, passed: bool) -> str:
    if bool(trace.get(f"STAGE_{stage}_CACHE_HIT")):
        return "CACHE_HIT"
    return "PASS" if passed else "WAITING"


def bind_case_status(
    *,
    snapshot: dict[str, Any] | None,
    result: dict[str, Any] | None,
    orchestration_status: str | None = None,
    error_code: str | None = None,
) -> dict[str, Any]:
    """Bind frozen Runtime/Pipeline truth to the UED state contract."""
    if error_code == "CANDIDATE_ASSET_COMMIT_FAILED":
        trace = (result or {}).get("latency_trace") or {}
        return {
            "parse": "PASS" if snapshot else "WAITING",
            "stage_a": _stage_status(trace, "A", True),
            "stage_b": _stage_status(trace, "B", True),
            "gate": "PASS",
            "result": "FAILED",
            "failed_stage": "CANDIDATE_ASSET_COMMIT",
            "error_code": error_code,
            "retryable": True,
        }
    if orchestration_status in {"RUNTIME_BLOCKED", "DEPENDENCY_BLOCKED"}:
        return {
            "parse": "PASS" if snapshot else "WAITING",
            "stage_a": "WAITING",
            "stage_b": "WAITING",
            "gate": "WAITING",
            "result": orchestration_status,
            "failed_stage": None,
            "error_code": error_code,
            "retryable": False,
        }
    if snapshot is None:
        return {
            "parse": "FAILED" if orchestration_status == "PARSE_FAILED" else "WAITING",
            "stage_a": "WAITING",
            "stage_b": "WAITING",
            "gate": "WAITING",
            "result": "FAILED" if orchestration_status == "PARSE_FAILED" else "QUEUED",
            "failed_stage": "PARSE" if orchestration_status == "PARSE_FAILED" else None,
            "error_code": error_code,
            "retryable": orchestration_status == "PARSE_FAILED",
        }
    if orchestration_status == "RUNNING":
        trace = (result or {}).get("latency_trace") or {}
        previous_failed_stage = str(
            (result or {}).get("failed_stage") or ""
        ).upper() or None
        if previous_failed_stage == "STAGE_B":
            stage_a = _stage_status(trace, "A", True)
            stage_b = "RUNNING"
        else:
            stage_a = "RUNNING"
            stage_b = "WAITING"
        return {
            "parse": "PASS",
            "stage_a": stage_a,
            "stage_b": stage_b,
            "gate": "WAITING",
            "result": "RUNNING",
            "failed_stage": (
                previous_failed_stage
                if previous_failed_stage in {"STAGE_A", "STAGE_B"}
                else None
            ),
            "error_code": error_code,
            "retryable": False,
        }
    if result is None:
        return {
            "parse": "PASS",
            "stage_a": "WAITING",
            "stage_b": "WAITING",
            "gate": "WAITING",
            "result": orchestration_status or "QUEUED",
            "failed_stage": None,
            "error_code": error_code,
            "retryable": False,
        }

    trace = result.get("latency_trace") if isinstance(result.get("latency_trace"), dict) else {}
    failed_stage = str(result.get("failed_stage") or "").upper() or None
    pipeline_status = str(result.get("pipeline_status") or "")
    gate_status = str(
        ((result.get("evidence_validation") or {}).get("status"))
        or "NOT_RUN"
    ).upper()

    if failed_stage == "STAGE_A":
        return {
            "parse": "PASS",
            "stage_a": "FAILED",
            "stage_b": "WAITING",
            "gate": "WAITING",
            "result": "FAILED",
            "failed_stage": "STAGE_A",
            "error_code": result.get("error_code"),
            "retryable": True,
        }
    if failed_stage == "STAGE_B":
        return {
            "parse": "PASS",
            "stage_a": _stage_status(trace, "A", True),
            "stage_b": "FAILED",
            "gate": "WAITING",
            "result": "FAILED",
            "failed_stage": "STAGE_B",
            "error_code": result.get("error_code"),
            "retryable": True,
        }

    stage_a = _stage_status(trace, "A", pipeline_status == "GOLDEN_PREVIEW_READY")
    stage_b = _stage_status(trace, "B", pipeline_status == "GOLDEN_PREVIEW_READY")
    if gate_status != "PASS":
        return {
            "parse": "PASS",
            "stage_a": stage_a,
            "stage_b": stage_b,
            "gate": "FAILED",
            "result": "FAILED",
            "failed_stage": "GATE",
            "error_code": result.get("error_code") or "EVIDENCE_GATE_FAILED",
            "retryable": True,
        }

    review_required = str(result.get("status") or "").upper() == "NEEDS_REVIEW"
    return {
        "parse": "PASS",
        "stage_a": stage_a,
        "stage_b": stage_b,
        "gate": "PASS",
        "result": "REVIEW" if review_required else "CANDIDATE_READY",
        "failed_stage": None,
        "error_code": None,
        "retryable": False,
    }


def aggregate_batch_status(items: list[dict[str, Any]]) -> str:
    if not items:
        return "EMPTY"
    states = [str(item.get("result") or item.get("orchestration_status") or "QUEUED") for item in items]
    if all(state == "QUEUED" for state in states):
        return "QUEUED"
    if any(state in {"RUNNING", "UPLOADING", "PARSING"} for state in states):
        return "RUNNING"
    failed = sum(state == "FAILED" for state in states)
    ready = sum(state in {"CANDIDATE_READY", "REVIEW"} for state in states)
    if failed == len(states):
        return "FAILED"
    if failed and ready:
        return "PARTIAL_FAILURE"
    if ready == len(states):
        return "READY_FOR_REVIEW"
    return "MIXED"


class HardwareR1WorkbenchStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS hardware_r1_batch (
                    batch_id TEXT PRIMARY KEY,
                    dataset_manifest_json TEXT,
                    dataset_frozen_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS hardware_r1_batch_item (
                    item_id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL,
                    business_case_id TEXT,
                    source_id TEXT,
                    source_file TEXT NOT NULL,
                    orchestration_status TEXT NOT NULL,
                    failed_stage TEXT,
                    error_code TEXT,
                    snapshot_json TEXT,
                    result_json TEXT,
                    candidate_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(batch_id) REFERENCES hardware_r1_batch(batch_id)
                );
                CREATE INDEX IF NOT EXISTS idx_hardware_r1_batch_item_batch
                ON hardware_r1_batch_item(batch_id, created_at);
                """
            )
            batch_columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(hardware_r1_batch)"
                ).fetchall()
            }
            if "dataset_manifest_json" not in batch_columns:
                connection.execute(
                    "ALTER TABLE hardware_r1_batch ADD COLUMN dataset_manifest_json TEXT"
                )
            if "dataset_frozen_at" not in batch_columns:
                connection.execute(
                    "ALTER TABLE hardware_r1_batch ADD COLUMN dataset_frozen_at TEXT"
                )
            columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(hardware_r1_batch_item)"
                ).fetchall()
            }
            if "candidate_id" not in columns:
                connection.execute(
                    "ALTER TABLE hardware_r1_batch_item ADD COLUMN candidate_id TEXT"
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_hardware_r1_batch_item_candidate "
                "ON hardware_r1_batch_item(candidate_id)"
            )

    def create_batch(self) -> str:
        batch_id = "HWB-" + uuid4().hex[:16]
        now = _utc_now()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO hardware_r1_batch(batch_id,created_at,updated_at) VALUES(?,?,?)",
                (batch_id, now, now),
            )
        return batch_id

    def freeze_dataset_identity(
        self,
        batch_id: str,
        entries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Persist the immutable upload identity before any Provider call."""
        now = _utc_now()
        manifest = {
            "version": "hardware-r1-dataset-identity/v1",
            "batch_id": str(batch_id),
            "frozen_at": now,
            "entries": [dict(entry) for entry in entries],
        }
        encoded = json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._connect() as connection:
            row = connection.execute(
                "SELECT dataset_manifest_json FROM hardware_r1_batch WHERE batch_id=?",
                (batch_id,),
            ).fetchone()
            if row is None:
                raise HardwareR1WorkbenchError("BATCH_NOT_FOUND")
            existing = row["dataset_manifest_json"]
            if existing:
                current = json.loads(existing)
                # A frozen Batch identity is immutable. Repeating the freeze is
                # allowed only when the exact entry list is unchanged.
                if current.get("entries") != manifest["entries"]:
                    raise HardwareR1WorkbenchError("DATASET_IDENTITY_ALREADY_FROZEN")
                return current
            connection.execute(
                """
                UPDATE hardware_r1_batch
                SET dataset_manifest_json=?,dataset_frozen_at=?,updated_at=?
                WHERE batch_id=?
                """,
                (encoded, now, now, batch_id),
            )
        return manifest

    def get_dataset_identity(self, batch_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT dataset_manifest_json
                FROM hardware_r1_batch
                WHERE batch_id=?
                """,
                (batch_id,),
            ).fetchone()
        if row is None:
            raise HardwareR1WorkbenchError("BATCH_NOT_FOUND")
        if not row["dataset_manifest_json"]:
            return None
        try:
            value = json.loads(row["dataset_manifest_json"])
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise HardwareR1WorkbenchError("DATASET_IDENTITY_CORRUPT") from error
        if (
            not isinstance(value, dict)
            or value.get("version") != "hardware-r1-dataset-identity/v1"
            or value.get("batch_id") != batch_id
            or not isinstance(value.get("entries"), list)
        ):
            raise HardwareR1WorkbenchError("DATASET_IDENTITY_CORRUPT")
        return value

    def add_item(
        self,
        batch_id: str,
        *,
        source_file: str,
        business_case_id: str | None = None,
        source_id: str | None = None,
        orchestration_status: str = "QUEUED",
        failed_stage: str | None = None,
        error_code: str | None = None,
        snapshot: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        candidate_id: str | None = None,
    ) -> str:
        item_id = "HWI-" + uuid4().hex[:16]
        now = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO hardware_r1_batch_item(
                    item_id,batch_id,business_case_id,source_id,source_file,
                    orchestration_status,failed_stage,error_code,
                    snapshot_json,result_json,candidate_id,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    item_id,
                    batch_id,
                    business_case_id,
                    source_id,
                    source_file,
                    orchestration_status,
                    failed_stage,
                    error_code,
                    json.dumps(snapshot, ensure_ascii=False) if snapshot is not None else None,
                    json.dumps(result, ensure_ascii=False) if result is not None else None,
                    candidate_id,
                    now,
                    now,
                ),
            )
            connection.execute(
                "UPDATE hardware_r1_batch SET updated_at=? WHERE batch_id=?",
                (now, batch_id),
            )
        return item_id

    def update_item(
        self,
        item_id: str,
        *,
        orchestration_status: str,
        failed_stage: str | None,
        error_code: str | None,
        result: dict[str, Any] | None,
        candidate_id: str | None = None,
    ) -> None:
        now = _utc_now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT batch_id FROM hardware_r1_batch_item WHERE item_id=?",
                (item_id,),
            ).fetchone()
            if row is None:
                raise HardwareR1WorkbenchError("BATCH_ITEM_NOT_FOUND")
            connection.execute(
                """
                UPDATE hardware_r1_batch_item
                SET orchestration_status=?,failed_stage=?,error_code=?,
                    result_json=?,candidate_id=COALESCE(?,candidate_id),updated_at=?
                WHERE item_id=?
                """,
                (
                    orchestration_status,
                    failed_stage,
                    error_code,
                    json.dumps(result, ensure_ascii=False) if result is not None else None,
                    candidate_id,
                    now,
                    item_id,
                ),
            )
            connection.execute(
                "UPDATE hardware_r1_batch SET updated_at=? WHERE batch_id=?",
                (now, row["batch_id"]),
            )

    @staticmethod
    def _item(row: sqlite3.Row) -> dict[str, Any]:
        snapshot = json.loads(row["snapshot_json"]) if row["snapshot_json"] else None
        result = json.loads(row["result_json"]) if row["result_json"] else None
        bound = bind_case_status(
            snapshot=snapshot,
            result=result,
            orchestration_status=row["orchestration_status"],
            error_code=row["error_code"],
        )
        candidate_id = str(row["candidate_id"] or "").strip() or None
        if bound["result"] in {"REVIEW", "CANDIDATE_READY"} and not candidate_id:
            bound = {
                **bound,
                "result": "FAILED",
                "failed_stage": "CANDIDATE_ASSET_COMMIT",
                "error_code": "CANDIDATE_ASSET_NOT_BOUND",
                "retryable": True,
            }
            orchestration_status = "FAILED"
        else:
            orchestration_status = row["orchestration_status"]
        trace = (result or {}).get("latency_trace") or {}
        duration_ms = int(trace.get("TOTAL_MS") or 0)
        if orchestration_status == "RUNNING":
            try:
                started = datetime.fromisoformat(str(row["updated_at"]))
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
                elapsed = int(
                    max(
                        0.0,
                        (datetime.now(timezone.utc) - started.astimezone(timezone.utc))
                        .total_seconds()
                        * 1000,
                    )
                )
                duration_ms = max(duration_ms, elapsed)
            except (TypeError, ValueError):
                pass
        return {
            "item_id": row["item_id"],
            "batch_id": row["batch_id"],
            "business_case_id": row["business_case_id"],
            "source_id": row["source_id"],
            "source_file": row["source_file"],
            "candidate_id": candidate_id,
            "orchestration_status": orchestration_status,
            "failed_stage": bound["failed_stage"],
            "error_code": bound["error_code"],
            "parse": bound["parse"],
            "stage_a": bound["stage_a"],
            "stage_b": bound["stage_b"],
            "gate": bound["gate"],
            "result": bound["result"],
            "retryable": bound["retryable"],
            "provider_calls": int((result or {}).get("provider_call_count") or 0),
            "duration_ms": duration_ms,
            "run_ref": (result or {}).get("run_id"),
            "updated_at": row["updated_at"],
            "snapshot": snapshot,
            "pipeline_result": result,
        }

    def get_item(self, item_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM hardware_r1_batch_item WHERE item_id=?",
                (item_id,),
            ).fetchone()
        if row is None:
            raise HardwareR1WorkbenchError("BATCH_ITEM_NOT_FOUND")
        return self._item(row)

    def list_items(self, batch_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM hardware_r1_batch_item
                WHERE batch_id=?
                ORDER BY created_at,item_id
                """,
                (batch_id,),
            ).fetchall()
        return [self._item(row) for row in rows]

    def list_batches(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT batch_id,created_at,updated_at
                FROM hardware_r1_batch
                ORDER BY created_at DESC LIMIT ?
                """,
                (max(1, min(int(limit), 200)),),
            ).fetchall()
        items = []
        for row in rows:
            batch_items = self.list_items(str(row["batch_id"]))
            items.append(
                {
                    "batch_id": row["batch_id"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "status": aggregate_batch_status(batch_items),
                    "summary": _summary(batch_items),
                }
            )
        return items


def _summary(items: list[dict[str, Any]]) -> dict[str, int]:
    result = {
        "TOTAL": len(items),
        "QUEUED": 0,
        "RUNNING": 0,
        "CANDIDATE_READY": 0,
        "REVIEW": 0,
        "FAILED": 0,
    }
    for item in items:
        state = str(item.get("result") or "")
        if state in result:
            result[state] += 1
        elif item.get("orchestration_status") == "RUNNING":
            result["RUNNING"] += 1
    return result


class HardwareR1WorkbenchService:
    def apply_human_review(
        self,
        item_id: str,
        *,
        decision: str,
        reviewer: str,
        reason: str,
        confirmed_content: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Apply an explicit human decision to the Durable Candidate.

        CONFIRM may correct editable knowledge fields. Source identity, provenance,
        Evidence and Candidate contract identity remain server-owned and immutable.
        DEFER/REJECT keep the Candidate blocked from Promotion without deleting it.
        """
        decision_name = str(decision or "").strip().upper()
        reviewer_name = str(reviewer or "").strip()
        reason_text = str(reason or "").strip()
        if (
            decision_name not in {"CONFIRM", "DEFER", "REJECT"}
            or not reviewer_name
            or not reason_text
        ):
            raise HardwareR1WorkbenchError("REVIEW_DECISION_INVALID")

        item = self.store.get_item(item_id)
        candidate_id = str(item.get("candidate_id") or "").strip()
        if not candidate_id:
            raise HardwareR1WorkbenchError("CANDIDATE_NOT_FOUND")
        getter = getattr(self.candidate_repository, "get_candidate", None)
        if not callable(getter):
            raise HardwareR1WorkbenchError("CANDIDATE_NOT_FOUND")
        try:
            asset = getter(candidate_id)
        except CandidateAssetRepositoryError as error:
            raise HardwareR1WorkbenchError(error.code) from error
        if not isinstance(asset, dict):
            raise HardwareR1WorkbenchError("CANDIDATE_NOT_FOUND")
        if str(asset.get("asset_status") or "").upper() != "ACTIVE":
            raise HardwareR1WorkbenchError("CANDIDATE_ASSET_INVALIDATED")
        if str(asset.get("promotion_status") or "").upper() != "NOT_STARTED":
            raise HardwareR1WorkbenchError("CANDIDATE_LOCKED_BY_PROMOTION")
        try:
            expected_row_version = int(asset["row_version"])
        except (KeyError, TypeError, ValueError) as error:
            raise HardwareR1WorkbenchError("CANDIDATE_REVIEW_TRANSITION_INVALID") from error

        pipeline_result = item.get("pipeline_result") or {}
        if decision_name in {"DEFER", "REJECT"}:
            recorder = getattr(
                self.candidate_repository,
                "record_production_review_decision",
                None,
            )
            if not callable(recorder):
                raise HardwareR1WorkbenchError(
                    "CANDIDATE_REVIEW_TRANSITION_INVALID"
                )
            try:
                recorder(
                    candidate_id,
                    expected_row_version=expected_row_version,
                    reviewer=reviewer_name,
                    disposition=(
                        "DEFERRED" if decision_name == "DEFER" else "REJECTED"
                    ),
                    reason=reason_text,
                )
            except CandidateAssetRepositoryError as error:
                raise HardwareR1WorkbenchError(error.code) from error
            self.store.update_item(
                item_id,
                orchestration_status="REVIEW",
                failed_stage=None,
                error_code=None,
                result=pipeline_result,
            )
            return self.get_item(item_id)

        if not isinstance(confirmed_content, dict):
            raise HardwareR1WorkbenchError("REVIEW_CONFIRMED_CONTENT_REQUIRED")
        original = deepcopy(asset.get("knowledge_object"))
        if not isinstance(original, dict):
            raise HardwareR1WorkbenchError("CANDIDATE_NOT_FOUND")

        # The client may edit business knowledge sections only.  Identity,
        # source/evidence/provenance, conflicts and review metadata are
        # reconstructed from the authoritative Durable Candidate.
        reviewed = deepcopy(original)

        def merge_review_values(target: Any, incoming: Any) -> None:
            if isinstance(target, dict):
                if "value" in target:
                    if isinstance(incoming, dict):
                        if "value" in incoming:
                            target["value"] = deepcopy(incoming["value"])
                        # Key-parameter business semantics are editable, but
                        # Evidence/status/confidence/warnings remain server-owned.
                        if "name" in target and "name" in incoming:
                            target["name"] = deepcopy(incoming["name"])
                        if "unit" in target and "unit" in incoming:
                            target["unit"] = deepcopy(incoming["unit"])
                    return
                if not isinstance(incoming, dict):
                    return
                for key, child in target.items():
                    if key in incoming:
                        merge_review_values(child, incoming[key])
                return
            if isinstance(target, list) and isinstance(incoming, list):
                for index, child in enumerate(target):
                    if index < len(incoming):
                        merge_review_values(child, incoming[index])

        # Only business field values are editable. Evidence bindings,
        # extraction status/confidence, derived-from metadata and every
        # Source/identity/provenance field remain server-owned.
        for key in (
            "engineering_context",
            "observed_problem",
            "engineering_analysis",
            "engineering_resolution",
            "reusable_knowledge",
            "facts",  # compatibility with earlier Candidate fixtures
        ):
            if key in reviewed and key in confirmed_content:
                merge_review_values(reviewed[key], confirmed_content[key])

        reviewed_at = _utc_now()
        conflicts = reviewed.get("conflicts")
        if not isinstance(conflicts, list):
            conflicts = []
            reviewed["conflicts"] = conflicts
        review = reviewed.get("review")
        if not isinstance(review, dict):
            review = {}
            reviewed["review"] = review
        decisions = review.get("field_decisions")
        if not isinstance(decisions, list):
            decisions = []
            review["field_decisions"] = decisions

        for conflict in conflicts:
            if not isinstance(conflict, dict):
                continue
            if (
                str(conflict.get("status") or "").upper() != "OPEN"
                and str(conflict.get("resolution_status") or "").upper()
                != "NEEDS_REVIEW"
            ):
                continue
            conflict_id = str(conflict.get("conflict_id") or "")
            field = str(conflict.get("field") or "")
            conflict["status"] = "RESOLVED"
            conflict["resolution_status"] = "CONFIRMED"
            conflict["reviewer_note"] = reason_text
            conflict["resolution"] = {
                "decision_source": "HUMAN_REVIEW",
                "reviewer": reviewer_name,
                "reviewed_at": reviewed_at,
            }
            decisions.append(
                {
                    "conflict_id": conflict_id,
                    "field": field,
                    "decision_source": "HUMAN_REVIEW",
                    "reviewer": reviewer_name,
                    "reviewed_at": reviewed_at,
                }
            )
        review["object_status"] = "CANDIDATE"
        review["reviewer"] = reviewer_name
        review["reviewed_at"] = reviewed_at

        apply_review = getattr(
            self.candidate_repository, "apply_production_review", None
        )
        if not callable(apply_review):
            raise HardwareR1WorkbenchError(
                "CANDIDATE_REVIEW_TRANSITION_INVALID"
            )
        try:
            apply_review(
                candidate_id,
                reviewed_knowledge_object=reviewed,
                expected_row_version=expected_row_version,
                reviewer=reviewer_name,
                reason=reason_text,
            )
        except CandidateAssetRepositoryError as error:
            raise HardwareR1WorkbenchError(error.code) from error

        self.store.update_item(
            item_id,
            orchestration_status="CANDIDATE_READY",
            failed_stage=None,
            error_code=None,
            result=pipeline_result,
        )
        return self.get_item(item_id)

    def __init__(
        self,
        store: HardwareR1WorkbenchStore,
        *,
        source_store: Any,
        structurer_factory: Callable[[], Any],
        preview_store: Any | None = None,
        candidate_repository: Any | None = None,
    ):
        self.store = store
        self.source_store = source_store
        self.structurer_factory = structurer_factory
        self.preview_store = preview_store
        self.candidate_repository = candidate_repository

    def upload_batch(self, files: list[tuple[str, bytes, str | None]]) -> dict[str, Any]:
        batch_id = self.store.create_batch()
        dataset_entries: list[dict[str, Any]] = []
        for source_order, (filename, payload, mime_type) in enumerate(files, start=1):
            name = Path(str(filename or "").replace("\\", "/")).name
            dataset_entry: dict[str, Any] = {
                "order": source_order,
                "item_id": None,
                "source_file": name or "unknown",
                "business_case_id": None,
                "source_id": None,
                "sha256": hashlib.sha256(payload).hexdigest() if payload else None,
                "size_bytes": len(payload),
                "binding_status": "UNBOUND",
            }
            dataset_entries.append(dataset_entry)
            if not name.lower().endswith(".docx") or not payload:
                dataset_entry["binding_status"] = "PARSE_FAILED"
                dataset_entry["item_id"] = self.store.add_item(
                    batch_id,
                    source_file=name or "unknown",
                    orchestration_status="PARSE_FAILED",
                    failed_stage="PARSE",
                    error_code="DOCX_REQUIRED" if not name.lower().endswith(".docx") else "DOCX_EMPTY",
                )
                continue
            try:
                with TemporaryDirectory(prefix="hardware-r1-batch-") as temporary:
                    path = Path(temporary) / name
                    path.write_bytes(payload)
                    snapshot = parse_docx(path).to_snapshot()
                    snapshot["markdown_view"] = build_markdown_view(snapshot)
                identity = snapshot.get("identity") or {}
                case_id = str(identity.get("business_case_id") or "").strip()
                source_id = str(
                    ((snapshot.get("source") or {}).get("source_id"))
                    or identity.get("source_id")
                    or ""
                )
                if not case_id:
                    dataset_entry.update(
                        source_id=source_id or None,
                        binding_status="PARSE_FAILED",
                    )
                    dataset_entry["item_id"] = self.store.add_item(
                        batch_id,
                        source_file=name,
                        source_id=source_id or None,
                        orchestration_status="PARSE_FAILED",
                        failed_stage="PARSE",
                        error_code="BUSINESS_CASE_ID_REQUIRED_FOR_SOURCE_BINDING",
                        snapshot=snapshot,
                    )
                    continue
                register = getattr(self.source_store, "register_active_bytes", None)
                if not callable(register):
                    dataset_entry.update(
                        business_case_id=case_id,
                        source_id=source_id or None,
                        binding_status="DEPENDENCY_BLOCKED",
                    )
                    dataset_entry["item_id"] = self.store.add_item(
                        batch_id,
                        source_file=name,
                        business_case_id=case_id,
                        source_id=source_id or None,
                        orchestration_status="DEPENDENCY_BLOCKED",
                        error_code="SOURCE_BINDING_CLOSURE_REQUIRED",
                        snapshot=snapshot,
                    )
                    continue
                try:
                    binding = register(case_id, name, payload, mime_type=mime_type)
                except Exception as error:
                    code = str(getattr(error, "code", None) or "SOURCE_BINDING_FAILED")
                    dataset_entry.update(
                        business_case_id=case_id,
                        source_id=source_id or None,
                        binding_status="DEPENDENCY_BLOCKED",
                    )
                    dataset_entry["item_id"] = self.store.add_item(
                        batch_id,
                        source_file=name,
                        business_case_id=case_id,
                        source_id=source_id or None,
                        orchestration_status="DEPENDENCY_BLOCKED",
                        error_code=code,
                        snapshot=snapshot,
                    )
                    continue
                bound_source_id = str(binding.get("source_id") or source_id)
                dataset_entry.update(
                    business_case_id=case_id,
                    source_id=bound_source_id,
                    sha256=str(binding.get("sha256") or dataset_entry["sha256"] or ""),
                    size_bytes=int(binding.get("size_bytes") or len(payload)),
                    binding_status="BOUND",
                )
                dataset_entry["item_id"] = self.store.add_item(
                    batch_id,
                    source_file=name,
                    business_case_id=case_id,
                    source_id=bound_source_id,
                    orchestration_status="QUEUED",
                    snapshot=snapshot,
                )
            except Exception as error:
                dataset_entry["binding_status"] = "PARSE_FAILED"
                dataset_entry["item_id"] = self.store.add_item(
                    batch_id,
                    source_file=name,
                    orchestration_status="PARSE_FAILED",
                    failed_stage="PARSE",
                    error_code=str(getattr(error, "code", None) or "PARSE_FAILED"),
                )
        self.store.freeze_dataset_identity(batch_id, dataset_entries)
        return self.get_batch(batch_id)

    def run_batch(self, batch_id: str) -> dict[str, Any]:
        items = [
            self._resolve_candidate(item)
            for item in self.store.list_items(batch_id)
        ]
        for item in items:
            if item["result"] != "QUEUED":
                continue
            self._run_item(item, retry_stage=None, force_full_run=False)
        return self.get_batch(batch_id)

    def retry_failed_only(self, batch_id: str) -> dict[str, Any]:
        items = [
            self._resolve_candidate(item)
            for item in self.store.list_items(batch_id)
        ]
        selected = [
            item
            for item in items
            if item["result"] == "FAILED"
            and item["retryable"]
            and item["orchestration_status"] not in {"RUNNING", "QUEUED"}
        ]
        for item in selected:
            retry_stage = item["failed_stage"]
            # Gate failures do not invent a new Stage. Normal Run/Resume
            # revalidates current cache/Last Good under the frozen Pipeline.
            if retry_stage == "GATE":
                retry_stage = None
            self._run_item(
                item,
                retry_stage=retry_stage,
                force_full_run=False,
            )
        return {
            **self.get_batch(batch_id),
            "retry_selected_count": len(selected),
        }

    def run_resume_item(self, item_id: str) -> dict[str, Any]:
        item = self._resolve_candidate(self.store.get_item(item_id))
        if item["orchestration_status"] == "RUNNING":
            raise HardwareR1WorkbenchError("RETRY_NOT_ALLOWED")
        self._run_item(item, retry_stage=None, force_full_run=False)
        return self.get_item(item_id)

    def retry_failed_stage_item(self, item_id: str) -> dict[str, Any]:
        item = self._resolve_candidate(self.store.get_item(item_id))
        if item["result"] != "FAILED" or not item["retryable"]:
            raise HardwareR1WorkbenchError("RETRY_NOT_ALLOWED")
        retry_stage = item.get("failed_stage")
        if retry_stage not in {"STAGE_A", "STAGE_B"}:
            # Gate/parse failures have no frozen Stage retry identity. The
            # caller must use Run/Resume or correct the parse/source problem.
            raise HardwareR1WorkbenchError("RETRY_FAILED_STAGE_NOT_AVAILABLE")
        self._run_item(
            item,
            retry_stage=str(retry_stage),
            force_full_run=False,
        )
        return self.get_item(item_id)

    def force_full_run_item(self, item_id: str) -> dict[str, Any]:
        item = self._resolve_candidate(self.store.get_item(item_id))
        if item["orchestration_status"] == "RUNNING":
            raise HardwareR1WorkbenchError("RETRY_NOT_ALLOWED")
        self._run_item(item, retry_stage=None, force_full_run=True)
        return self.get_item(item_id)

    def _verify_frozen_source_identity(self, item: dict[str, Any]) -> None:
        """Fail closed before Provider work if an uploaded source was substituted."""
        manifest = self.store.get_dataset_identity(str(item["batch_id"]))
        # Legacy/unit-created batches may predate the E2E manifest. The formal
        # Web upload path always freezes one before returning from upload_batch.
        if manifest is None:
            return
        entry = next(
            (
                value
                for value in manifest["entries"]
                if isinstance(value, dict)
                and value.get("item_id") == item.get("item_id")
            ),
            None,
        )
        if not isinstance(entry, dict):
            raise HardwareR1WorkbenchError("DATASET_IDENTITY_MISMATCH")
        expected = {
            "source_file": str(item.get("source_file") or ""),
            "business_case_id": item.get("business_case_id"),
            "source_id": item.get("source_id"),
        }
        if any(entry.get(key) != value for key, value in expected.items()):
            raise HardwareR1WorkbenchError("DATASET_IDENTITY_MISMATCH")
        if entry.get("binding_status") != "BOUND":
            return
        get_active_source = getattr(self.source_store, "get_active_source", None)
        if not callable(get_active_source):
            raise HardwareR1WorkbenchError("SOURCE_BINDING_CLOSURE_REQUIRED")
        try:
            source = get_active_source(str(item.get("business_case_id") or ""))
        except Exception as error:
            code = str(
                getattr(error, "code", None)
                or "ACTIVE_SOURCE_BINDING_REQUIRED"
            )
            raise HardwareR1WorkbenchError(code) from error
        if (
            str(source.get("binding_status") or "").upper() != "ACTIVE"
            or str(source.get("source_id") or "") != str(entry.get("source_id") or "")
            or str(source.get("sha256") or "") != str(entry.get("sha256") or "")
            or int(source.get("size_bytes") or -1) != int(entry.get("size_bytes") or -2)
        ):
            raise HardwareR1WorkbenchError("DATASET_SOURCE_IDENTITY_MISMATCH")

    def _run_item(
        self,
        item: dict[str, Any],
        *,
        retry_stage: str | None,
        force_full_run: bool,
    ) -> None:
        snapshot = item.get("snapshot")
        if not isinstance(snapshot, dict):
            self.store.update_item(
                item["item_id"],
                orchestration_status="FAILED",
                failed_stage="PARSE",
                error_code="SNAPSHOT_REQUIRED",
                result=None,
            )
            return
        try:
            self._verify_frozen_source_identity(item)
        except HardwareR1WorkbenchError as error:
            self.store.update_item(
                item["item_id"],
                orchestration_status="DEPENDENCY_BLOCKED",
                failed_stage=item.get("failed_stage"),
                error_code=error.code,
                result=item.get("pipeline_result"),
            )
            return
        self.store.update_item(
            item["item_id"],
            orchestration_status="RUNNING",
            failed_stage=item.get("failed_stage"),
            error_code=None,
            result=item.get("pipeline_result"),
        )
        try:
            structurer = self.structurer_factory()
            result = run_r1_agent_extraction(
                snapshot,
                structurer,
                force_retry=force_full_run,
                retry_failed_stage=(
                    retry_stage if retry_stage in {"STAGE_A", "STAGE_B"} else None
                ),
            )
            bound = bind_case_status(snapshot=snapshot, result=result)
            candidate_id = None
            if bound["result"] in {"REVIEW", "CANDIDATE_READY"}:
                try:
                    committed = self._commit_candidate_asset(item, snapshot, result)
                    candidate_id = str(committed.get("candidate_id") or "").strip()
                    if not candidate_id:
                        raise HardwareR1WorkbenchError("CANDIDATE_ID_MISSING")
                    bound["result"] = (
                        "REVIEW"
                        if bound["result"] == "REVIEW"
                        or str(committed.get("production_review_status") or "").upper()
                        == "REQUIRED"
                        else "CANDIDATE_READY"
                    )
                except Exception:
                    self.store.update_item(
                        item["item_id"],
                        orchestration_status="FAILED",
                        failed_stage="CANDIDATE_ASSET_COMMIT",
                        error_code="CANDIDATE_ASSET_COMMIT_FAILED",
                        result=result,
                    )
                    return

            # Preview storage is an expendable view, never Candidate truth.
            if self.preview_store is not None:
                try:
                    result["preview"] = self.preview_store.save(snapshot, result)
                except Exception as preview_error:
                    result["preview_error_code"] = str(
                        getattr(preview_error, "code", None)
                        or "PREVIEW_SAVE_FAILED"
                    )
            self.store.update_item(
                item["item_id"],
                orchestration_status=bound["result"],
                failed_stage=bound["failed_stage"],
                error_code=bound["error_code"],
                result=result,
                candidate_id=candidate_id,
            )
        except Exception as error:
            self.store.update_item(
                item["item_id"],
                orchestration_status="RUNTIME_BLOCKED",
                failed_stage=item.get("failed_stage"),
                error_code=str(
                    getattr(error, "code", None)
                    or "RUNTIME_EXECUTION_FAILED"
                ),
                result=item.get("pipeline_result"),
            )

    def _commit_candidate_asset(
        self,
        item: dict[str, Any],
        snapshot: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        repository = self.candidate_repository
        commit = getattr(repository, "create_or_commit_candidate", None)
        if not callable(commit):
            raise HardwareR1WorkbenchError("CANDIDATE_ASSET_REPOSITORY_UNAVAILABLE")
        if result.get("pipeline_status") != "GOLDEN_PREVIEW_READY":
            raise HardwareR1WorkbenchError("PIPELINE_GATE_NOT_PASSED")
        evidence_gate = result.get("evidence_validation")
        if not isinstance(evidence_gate, dict) or str(
            evidence_gate.get("status") or ""
        ).upper() != "PASS":
            raise HardwareR1WorkbenchError("EVIDENCE_GATE_NOT_PASSED")
        knowledge_object = result.get("knowledge_object")
        if not isinstance(knowledge_object, dict):
            raise HardwareR1WorkbenchError("KNOWLEDGE_OBJECT_REQUIRED")

        identity = snapshot.get("identity") or {}
        business_case_id = str(
            item.get("business_case_id") or identity.get("business_case_id") or ""
        ).strip()
        source = self.source_store.get_active_source(business_case_id)
        source_id = str(source.get("source_id") or "").strip()
        expected_source_id = str(
            item.get("source_id")
            or (snapshot.get("source") or {}).get("source_id")
            or ""
        ).strip()
        source_ref = str(source.get("source_ref") or "").strip()
        if (
            not business_case_id
            or not source_id
            or source_id != expected_source_id
            or not source_ref
            or str(source.get("binding_status") or "").upper() != "ACTIVE"
        ):
            raise HardwareR1WorkbenchError("ACTIVE_SOURCE_BINDING_REQUIRED")

        runtime = result.get("runtime") if isinstance(result.get("runtime"), dict) else {}
        stage_a = runtime.get("stage_a") if isinstance(runtime.get("stage_a"), dict) else {}
        stage_b = runtime.get("stage_b") if isinstance(runtime.get("stage_b"), dict) else {}
        provenance = (
            knowledge_object.get("provenance")
            if isinstance(knowledge_object.get("provenance"), dict)
            else {}
        )
        config_versions = [
            (name, str(stage.get("agent_config_version") or "").strip())
            for name, stage in (("stage_a", stage_a), ("stage_b", stage_b))
            if str(stage.get("agent_config_version") or "").strip()
        ]
        agent_config_version = ";".join(
            f"{name}={version}" for name, version in config_versions
        ) or str(provenance.get("agent_config_version") or "").strip()
        pipeline_version = str(result.get("pipeline_version") or "").strip()
        knowledge_schema_version = str(
            result.get("knowledge_object_contract_version")
            or knowledge_object.get("contract_version")
            or ""
        ).strip()
        validator_version = (
            f"stage_a={R1_STAGE_A_VALIDATOR_VERSION};"
            f"stage_b={R1_STAGE_B_VALIDATOR_VERSION}"
        )
        if not all(
            (agent_config_version, pipeline_version, knowledge_schema_version)
        ):
            raise HardwareR1WorkbenchError("CANDIDATE_VERSION_METADATA_REQUIRED")
        return commit(
            business_case_id=business_case_id,
            source_id=source_id,
            source_ref=source_ref,
            knowledge_object=knowledge_object,
            generation_run_id=(str(result.get("run_id") or "").strip() or None),
            pipeline_version=pipeline_version,
            agent_config_version=agent_config_version,
            knowledge_schema_version=knowledge_schema_version,
            validator_version=validator_version,
        )

    def _resolve_candidate(self, item: dict[str, Any]) -> dict[str, Any]:
        candidate_id = str(item.get("candidate_id") or "").strip()
        if not candidate_id:
            return {**item, "candidate": None, "candidate_asset": None}
        getter = getattr(self.candidate_repository, "get_candidate", None)
        try:
            asset = getter(candidate_id) if callable(getter) else None
        except Exception:
            asset = None
        if (
            not isinstance(asset, dict)
            or str(asset.get("candidate_id") or "") != candidate_id
            or str(asset.get("business_case_id") or "")
            != str(item.get("business_case_id") or "")
            or str(asset.get("source_id") or "") != str(item.get("source_id") or "")
            or not isinstance(asset.get("knowledge_object"), dict)
        ):
            return {
                **item,
                "result": "FAILED",
                "orchestration_status": "FAILED",
                "failed_stage": "CANDIDATE_ASSET_READ",
                "error_code": "CANDIDATE_ASSET_NOT_FOUND",
                "retryable": True,
                "candidate": None,
                "candidate_asset": None,
            }
        result = str(item.get("result") or "")
        if str(asset.get("asset_status") or "").upper() != "ACTIVE":
            return {
                **item,
                "result": "FAILED",
                "orchestration_status": "FAILED",
                "failed_stage": "CANDIDATE_ASSET_READ",
                "error_code": "CANDIDATE_ASSET_NOT_ACTIVE",
                "retryable": False,
                "candidate": asset["knowledge_object"],
                "candidate_asset": asset,
            }
        if result in {"REVIEW", "CANDIDATE_READY"}:
            result = (
                "REVIEW"
                if str(asset.get("production_review_status") or "").upper()
                == "REQUIRED"
                else "CANDIDATE_READY"
            )
        return {
            **item,
            "result": result,
            "candidate": asset["knowledge_object"],
            "candidate_asset": asset,
        }

    def list_batches(self, limit: int = 50) -> dict[str, Any]:
        batches = self.store.list_batches(limit=limit)
        items = []
        for batch in batches:
            batch_items = [
                self._resolve_candidate(item)
                for item in self.store.list_items(str(batch["batch_id"]))
            ]
            items.append(
                {
                    **batch,
                    "status": aggregate_batch_status(batch_items),
                    "summary": _summary(batch_items),
                }
            )
        return {
            "contract_version": WORKBENCH_CONTRACT_VERSION,
            "items": items,
            "total": len(items),
        }

    def resolve_review_conflict(
        self,
        item_id: str,
        *,
        conflict_id: str,
        decision_source: str,
        reviewer: str,
    ) -> dict[str, Any]:
        item = self.store.get_item(item_id)
        if item["result"] != "REVIEW":
            raise HardwareR1WorkbenchError("REVIEW_NOT_REQUIRED")

        candidate_id = str(item.get("candidate_id") or "").strip()
        if not candidate_id:
            raise HardwareR1WorkbenchError("CANDIDATE_NOT_FOUND")
        getter = getattr(self.candidate_repository, "get_candidate", None)
        if not callable(getter):
            raise HardwareR1WorkbenchError("CANDIDATE_NOT_FOUND")
        try:
            asset = getter(candidate_id)
        except CandidateAssetRepositoryError as error:
            raise HardwareR1WorkbenchError(error.code) from error
        if not isinstance(asset, dict) or str(asset.get("candidate_id") or "") != candidate_id:
            raise HardwareR1WorkbenchError("CANDIDATE_NOT_FOUND")
        if str(asset.get("asset_status") or "").upper() != "ACTIVE":
            raise HardwareR1WorkbenchError("CANDIDATE_ASSET_INVALIDATED")
        if str(asset.get("promotion_status") or "").upper() != "NOT_STARTED":
            raise HardwareR1WorkbenchError("CANDIDATE_LOCKED_BY_PROMOTION")
        if str(asset.get("production_review_status") or "").upper() != "REQUIRED":
            raise HardwareR1WorkbenchError(
                "CANDIDATE_REVIEW_TRANSITION_INVALID"
            )
        try:
            expected_row_version = int(asset["row_version"])
        except (KeyError, TypeError, ValueError) as error:
            raise HardwareR1WorkbenchError("CANDIDATE_REVIEW_TRANSITION_INVALID") from error

        candidate = deepcopy(asset.get("knowledge_object"))
        if not isinstance(candidate, dict):
            raise HardwareR1WorkbenchError("CANDIDATE_NOT_FOUND")

        conflicts = candidate.get("conflicts")
        if not isinstance(conflicts, list):
            raise HardwareR1WorkbenchError("REVIEW_CONFLICT_NOT_FOUND")

        conflict = next(
            (
                value
                for value in conflicts
                if isinstance(value, dict)
                and str(value.get("conflict_id") or "") == str(conflict_id)
            ),
            None,
        )
        if conflict is None:
            raise HardwareR1WorkbenchError("REVIEW_CONFLICT_NOT_FOUND")
        if (
            str(conflict.get("status") or "").upper() != "OPEN"
            or str(conflict.get("resolution_status") or "").upper()
            != "NEEDS_REVIEW"
        ):
            raise HardwareR1WorkbenchError("REVIEW_CONFLICT_ALREADY_RESOLVED")

        source_values = [
            value
            for value in conflict.get("source_values") or []
            if isinstance(value, dict)
        ]
        selected = next(
            (
                value
                for value in source_values
                if str(value.get("source") or "") == str(decision_source)
            ),
            None,
        )
        if selected is None:
            raise HardwareR1WorkbenchError("REVIEW_DECISION_SOURCE_INVALID")
        chosen_value = selected.get("value")
        field = str(conflict.get("field") or "")

        # V1 closure only supports the frozen title/body subject conflict.
        # Other review semantics must be versioned rather than guessed here.
        if (
            str(conflict.get("type") or "")
            != "TITLE_CONTENT_SUBJECT_MISMATCH"
            or field != "primary_subject"
        ):
            raise HardwareR1WorkbenchError("REVIEW_FIELD_NOT_SUPPORTED")
        engineering = candidate.get("engineering_context")
        if not isinstance(engineering, dict):
            raise HardwareR1WorkbenchError("REVIEW_FIELD_NOT_SUPPORTED")
        primary = engineering.get("primary_subject")
        if not isinstance(primary, dict):
            raise HardwareR1WorkbenchError("REVIEW_FIELD_NOT_SUPPORTED")
        primary["value"] = chosen_value

        reviewed_at = _utc_now()
        reviewer_name = str(reviewer or "MAINTAINER").strip() or "MAINTAINER"
        conflict["status"] = "RESOLVED"
        conflict["resolution_status"] = "CONFIRMED"
        conflict["reviewer_note"] = (
            f"Selected {decision_source}: {chosen_value}"
        )
        conflict["resolution"] = {
            "decision_source": str(decision_source),
            "selected_value": chosen_value,
            "reviewer": reviewer_name,
            "reviewed_at": reviewed_at,
        }

        review = candidate.setdefault("review", {})
        decisions = review.setdefault("field_decisions", [])
        if not isinstance(decisions, list):
            decisions = []
            review["field_decisions"] = decisions
        decisions.append(
            {
                "conflict_id": str(conflict_id),
                "field": field,
                "decision_source": str(decision_source),
                "selected_value": chosen_value,
                "reviewer": reviewer_name,
                "reviewed_at": reviewed_at,
            }
        )
        review["reviewer"] = reviewer_name
        review["reviewed_at"] = reviewed_at

        remaining = [
            value
            for value in conflicts
            if isinstance(value, dict)
            and (
                str(value.get("status") or "").upper() == "OPEN"
                or str(value.get("resolution_status") or "").upper()
                == "NEEDS_REVIEW"
            )
        ]
        if remaining:
            raise HardwareR1WorkbenchError(
                "CANDIDATE_REVIEW_TRANSITION_INVALID"
            )

        pipeline_result = item.get("pipeline_result") or {}
        provider_calls_before = int(pipeline_result.get("provider_call_count") or 0)
        reason = (
            f"Resolved {field} conflict {conflict_id} "
            f"using {decision_source}"
        )
        apply_review = getattr(
            self.candidate_repository, "apply_production_review", None
        )
        if not callable(apply_review):
            raise HardwareR1WorkbenchError(
                "CANDIDATE_REVIEW_TRANSITION_INVALID"
            )
        try:
            apply_review(
                candidate_id,
                reviewed_knowledge_object=candidate,
                expected_row_version=expected_row_version,
                reviewer=reviewer_name,
                reason=reason,
            )
        except CandidateAssetRepositoryError as error:
            raise HardwareR1WorkbenchError(error.code) from error

        # Keep result_json as the original Pipeline / execution snapshot.
        self.store.update_item(
            item_id,
            orchestration_status="CANDIDATE_READY",
            failed_stage=None,
            error_code=None,
            result=pipeline_result,
        )
        resolved = self.get_item(item_id)
        if int(resolved.get("provider_calls") or 0) != provider_calls_before:
            raise HardwareR1WorkbenchError("REVIEW_PROVIDER_CALL_CHANGED")
        return resolved

    def get_item(self, item_id: str) -> dict[str, Any]:
        item = self._resolve_candidate(self.store.get_item(item_id))
        result = item.get("pipeline_result") or {}
        review_history: list[dict[str, Any]] = []
        candidate_id = str(item.get("candidate_id") or "").strip()
        history_reader = getattr(
            self.candidate_repository, "list_production_reviews", None
        )
        if candidate_id and callable(history_reader):
            try:
                review_history = history_reader(candidate_id)
            except CandidateAssetRepositoryError as error:
                raise HardwareR1WorkbenchError(error.code) from error
        return {
            "contract_version": WORKBENCH_CONTRACT_VERSION,
            **item,
            "candidate": item.get("candidate"),
            "candidate_asset": item.get("candidate_asset"),
            "evidence_validation": result.get("evidence_validation"),
            "review_history": review_history,
        }

    def get_batch(self, batch_id: str) -> dict[str, Any]:
        items = [
            self._resolve_candidate(item)
            for item in self.store.list_items(batch_id)
        ]
        if not items and batch_id not in {item["batch_id"] for item in self.store.list_batches()}:
            # Empty batches are valid; list_batches is the authoritative existence check.
            batches = self.store.list_batches()
            if batch_id not in {item["batch_id"] for item in batches}:
                raise HardwareR1WorkbenchError("BATCH_NOT_FOUND")
        return {
            "contract_version": WORKBENCH_CONTRACT_VERSION,
            "batch_id": batch_id,
            "status": aggregate_batch_status(items),
            "summary": _summary(items),
            "dataset_identity": self.store.get_dataset_identity(batch_id),
            "items": items,
        }

    def advanced_debug(self, item_id: str) -> dict[str, Any]:
        item = self.store.get_item(item_id)
        result = item.get("pipeline_result") or {}
        trace = result.get("latency_trace") or {}
        return {
            "contract_version": WORKBENCH_CONTRACT_VERSION,
            "item_id": item_id,
            "batch_id": item["batch_id"],
            "read_only": True,
            "execution_mode": result.get("execution_mode"),
            "provider_calls": result.get("provider_call_count", 0),
            "validation_retry": result.get("validation_retry_count", 0),
            "stage_a": {
                "cache_hit": trace.get("STAGE_A_CACHE_HIT"),
                "recovery_calls": trace.get("STAGE_A_CACHE_RECOVERY_CALL_COUNT"),
                "transport_retry": trace.get("STAGE_A_TRANSPORT_RETRY_COUNT"),
                "validation_retry": trace.get("STAGE_A_VALIDATION_RETRY_COUNT"),
                "input_chars": trace.get("STAGE_A_INPUT_CHARS"),
                "input_bytes": trace.get("STAGE_A_INPUT_BYTES"),
                "output_chars": trace.get("STAGE_A_OUTPUT_CHARS"),
                "output_bytes": trace.get("STAGE_A_OUTPUT_BYTES"),
                "prompt_tokens": trace.get("STAGE_A_PROMPT_TOKENS", "UNKNOWN"),
                "completion_tokens": trace.get("STAGE_A_COMPLETION_TOKENS", "UNKNOWN"),
                "attempts": trace.get("STAGE_A_PROVIDER_ATTEMPTS") or [],
            },
            "stage_b": {
                "cache_hit": trace.get("STAGE_B_CACHE_HIT"),
                "recovery_calls": trace.get("STAGE_B_CACHE_RECOVERY_CALL_COUNT"),
                "transport_retry": trace.get("STAGE_B_TRANSPORT_RETRY_COUNT"),
                "validation_retry": trace.get("STAGE_B_VALIDATION_RETRY_COUNT"),
                "input_chars": trace.get("STAGE_B_INPUT_CHARS"),
                "input_bytes": trace.get("STAGE_B_INPUT_BYTES"),
                "output_chars": trace.get("STAGE_B_OUTPUT_CHARS"),
                "output_bytes": trace.get("STAGE_B_OUTPUT_BYTES"),
                "prompt_tokens": trace.get("STAGE_B_PROMPT_TOKENS", "UNKNOWN"),
                "completion_tokens": trace.get("STAGE_B_COMPLETION_TOKENS", "UNKNOWN"),
                "attempts": trace.get("STAGE_B_PROVIDER_ATTEMPTS") or [],
            },
            "cache_key_version": trace.get("CACHE_KEY_VERSION"),
            "pipeline_version": result.get("pipeline_version"),
            "execution_trace_version": result.get("execution_trace_version"),
            "failed_stage": item.get("failed_stage"),
            "failure_code": item.get("error_code"),
            "run_ref": item.get("run_ref"),
            "duration_ms": item.get("duration_ms"),
        }


__all__ = [
    "WORKBENCH_CONTRACT_VERSION",
    "HardwareR1WorkbenchError",
    "HardwareR1WorkbenchService",
    "HardwareR1WorkbenchStore",
    "aggregate_batch_status",
    "bind_case_status",
]
