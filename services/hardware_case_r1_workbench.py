"""Hardware R1 Batch-first Knowledge Production Workbench.

This layer is orchestration only. It reuses the frozen R1 parser, Stage A/B
pipeline, validation/cache semantics and Golden Knowledge Object. It never
publishes Formal Knowledge and never becomes a second Knowledge store.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable
from uuid import uuid4

from services.hardware_case_markdown_agent import (
    build_markdown_view,
    run_r1_agent_extraction,
)
from services.hardware_case_word import parse_docx


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
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(batch_id) REFERENCES hardware_r1_batch(batch_id)
                );
                CREATE INDEX IF NOT EXISTS idx_hardware_r1_batch_item_batch
                ON hardware_r1_batch_item(batch_id, created_at);
                """
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
    ) -> str:
        item_id = "HWI-" + uuid4().hex[:16]
        now = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO hardware_r1_batch_item(
                    item_id,batch_id,business_case_id,source_id,source_file,
                    orchestration_status,failed_stage,error_code,
                    snapshot_json,result_json,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
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
                    result_json=?,updated_at=?
                WHERE item_id=?
                """,
                (
                    orchestration_status,
                    failed_stage,
                    error_code,
                    json.dumps(result, ensure_ascii=False) if result is not None else None,
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
        trace = (result or {}).get("latency_trace") or {}
        return {
            "item_id": row["item_id"],
            "batch_id": row["batch_id"],
            "business_case_id": row["business_case_id"],
            "source_id": row["source_id"],
            "source_file": row["source_file"],
            "orchestration_status": row["orchestration_status"],
            "failed_stage": bound["failed_stage"],
            "error_code": bound["error_code"],
            "parse": bound["parse"],
            "stage_a": bound["stage_a"],
            "stage_b": bound["stage_b"],
            "gate": bound["gate"],
            "result": bound["result"],
            "retryable": bound["retryable"],
            "provider_calls": int((result or {}).get("provider_call_count") or 0),
            "duration_ms": int(trace.get("TOTAL_MS") or 0),
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
    def __init__(
        self,
        store: HardwareR1WorkbenchStore,
        *,
        source_store: Any,
        structurer_factory: Callable[[], Any],
        preview_store: Any | None = None,
    ):
        self.store = store
        self.source_store = source_store
        self.structurer_factory = structurer_factory
        self.preview_store = preview_store

    def upload_batch(self, files: list[tuple[str, bytes, str | None]]) -> dict[str, Any]:
        batch_id = self.store.create_batch()
        for filename, payload, mime_type in files:
            name = Path(str(filename or "").replace("\\", "/")).name
            if not name.lower().endswith(".docx") or not payload:
                self.store.add_item(
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
                    self.store.add_item(
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
                    self.store.add_item(
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
                    self.store.add_item(
                        batch_id,
                        source_file=name,
                        business_case_id=case_id,
                        source_id=source_id or None,
                        orchestration_status="DEPENDENCY_BLOCKED",
                        error_code=code,
                        snapshot=snapshot,
                    )
                    continue
                self.store.add_item(
                    batch_id,
                    source_file=name,
                    business_case_id=case_id,
                    source_id=str(binding.get("source_id") or source_id),
                    orchestration_status="QUEUED",
                    snapshot=snapshot,
                )
            except Exception as error:
                self.store.add_item(
                    batch_id,
                    source_file=name,
                    orchestration_status="PARSE_FAILED",
                    failed_stage="PARSE",
                    error_code=str(getattr(error, "code", None) or "PARSE_FAILED"),
                )
        return self.get_batch(batch_id)

    def run_batch(self, batch_id: str) -> dict[str, Any]:
        items = self.store.list_items(batch_id)
        for item in items:
            if item["result"] != "QUEUED":
                continue
            self._run_item(item, retry_stage=None, force_full_run=False)
        return self.get_batch(batch_id)

    def retry_failed_only(self, batch_id: str) -> dict[str, Any]:
        items = self.store.list_items(batch_id)
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
        item = self.store.get_item(item_id)
        if item["orchestration_status"] == "RUNNING":
            raise HardwareR1WorkbenchError("RETRY_NOT_ALLOWED")
        self._run_item(item, retry_stage=None, force_full_run=False)
        return self.store.get_item(item_id)

    def retry_failed_stage_item(self, item_id: str) -> dict[str, Any]:
        item = self.store.get_item(item_id)
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
        return self.store.get_item(item_id)

    def force_full_run_item(self, item_id: str) -> dict[str, Any]:
        item = self.store.get_item(item_id)
        if item["orchestration_status"] == "RUNNING":
            raise HardwareR1WorkbenchError("RETRY_NOT_ALLOWED")
        self._run_item(item, retry_stage=None, force_full_run=True)
        return self.store.get_item(item_id)

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
            if self.preview_store is not None:
                result["preview"] = self.preview_store.save(snapshot, result)
            bound = bind_case_status(snapshot=snapshot, result=result)
            self.store.update_item(
                item["item_id"],
                orchestration_status=bound["result"],
                failed_stage=bound["failed_stage"],
                error_code=bound["error_code"],
                result=result,
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

    def get_batch(self, batch_id: str) -> dict[str, Any]:
        items = self.store.list_items(batch_id)
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
