"""Production orchestration for R1 Golden Candidate -> Formal Knowledge.

This module owns only promotion workflow state. It reuses the frozen Hardware
R1 Golden bridge and Unified Knowledge public contracts. It is not a second
Knowledge store and never auto-publishes.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from urllib.parse import quote
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from services.hardware_r1_golden_knowledge_bridge import (
    HardwareR1GoldenBridgeError,
    HardwareR1GoldenKnowledgeBridge,
)
from services.hardware_asset_repository import (
    CandidateAssetRepository,
    CandidateAssetRepositoryError,
)
from services.hardware_asset_operation_journal import (
    HardwareAssetOperationJournal,
    HardwareAssetOperationJournalError,
)
from services.hardware_case_knowledge_adapter import HardwareKnowledgeAdapterError

PROMOTION_CONTRACT_VERSION = "hardware-r1-knowledge-promotion/v1"
PROMOTION_REVISION = 1

TERMINAL_SUCCESS = {"VERIFIED"}
FAILED_STATES = {
    "INTAKE_FAILED",
    "REVIEW_FAILED",
    "PUBLISH_FAILED",
    "VERIFY_FAILED",
}


class HardwareR1PromotionError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_hash(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class HardwareR1KnowledgePromotionStore:
    """Promotion ledger only; Formal Knowledge remains Unified Knowledge owned."""

    def __init__(self, db_path: str | Path, *, read_only: bool = True) -> None:
        self.db_path = Path(db_path)
        self.read_only = bool(read_only)
        if not self.read_only:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        if not self.read_only:
            connection = sqlite3.connect(self.db_path)
            connection.row_factory = sqlite3.Row
            return connection
        if not self.db_path.is_file():
            raise sqlite3.OperationalError("no such table: hardware_r1_knowledge_promotion")
        uri = f"file:{quote(str(self.db_path.resolve()))}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS hardware_r1_knowledge_promotion (
                    item_id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL,
                    business_case_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    golden_hash TEXT NOT NULL,
                    candidate_id TEXT,
                    evidence_refs_json TEXT,
                    status TEXT NOT NULL,
                    last_action TEXT NOT NULL,
                    error_code TEXT,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    review_status TEXT,
                    knowledge_id TEXT,
                    public_ref TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_hardware_r1_promotion_batch
                ON hardware_r1_knowledge_promotion(batch_id, updated_at);
                """
            )

    @staticmethod
    def _public(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "contract_version": PROMOTION_CONTRACT_VERSION,
            "item_id": row["item_id"],
            "batch_id": row["batch_id"],
            "business_case_id": row["business_case_id"],
            "source_id": row["source_id"],
            "golden_hash": row["golden_hash"],
            "candidate_id": row["candidate_id"],
            "evidence_refs": (
                json.loads(row["evidence_refs_json"])
                if row["evidence_refs_json"]
                else []
            ),
            "status": row["status"],
            "last_action": row["last_action"],
            "error_code": row["error_code"],
            "retry_count": int(row["retry_count"] or 0),
            "review_status": row["review_status"],
            "knowledge_id": row["knowledge_id"],
            "public_ref": row["public_ref"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "auto_publish": False,
        }

    def get(self, item_id: str) -> dict[str, Any] | None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM hardware_r1_knowledge_promotion WHERE item_id=?",
                    (str(item_id),),
                ).fetchone()
        except sqlite3.OperationalError as error:
            if "no such table" in str(error).lower():
                return None
            raise
        return None if row is None else self._public(row)

    def list_batch(self, batch_id: str) -> list[dict[str, Any]]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                """
                SELECT * FROM hardware_r1_knowledge_promotion
                WHERE batch_id=?
                ORDER BY created_at,item_id
                """,
                (str(batch_id),),
                ).fetchall()
        except sqlite3.OperationalError as error:
            if "no such table" in str(error).lower():
                return []
            raise
        return [self._public(row) for row in rows]

    def list_all(self) -> list[dict[str, Any]]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM hardware_r1_knowledge_promotion ORDER BY created_at,item_id"
                ).fetchall()
        except sqlite3.OperationalError as error:
            if "no such table" in str(error).lower():
                return []
            raise
        return [self._public(row) for row in rows]

    def ensure(
        self,
        *,
        item_id: str,
        batch_id: str,
        business_case_id: str,
        source_id: str,
        golden_hash: str,
    ) -> dict[str, Any]:
        if self.read_only:
            raise HardwareR1PromotionError("LEGACY_PROMOTION_READ_ONLY")
        current = self.get(item_id)
        if current is not None:
            if (
                current["batch_id"] != batch_id
                or current["business_case_id"] != business_case_id
                or current["source_id"] != source_id
                or current["golden_hash"] != golden_hash
            ):
                raise HardwareR1PromotionError("PROMOTION_IDEMPOTENCY_CONFLICT")
            return current

        now = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO hardware_r1_knowledge_promotion(
                    item_id,batch_id,business_case_id,source_id,golden_hash,
                    status,last_action,created_at,updated_at
                ) VALUES(?,?,?,?,?,'PRECHECK_PASS','PRECHECK',?,?)
                """,
                (
                    item_id,
                    batch_id,
                    business_case_id,
                    source_id,
                    golden_hash,
                    now,
                    now,
                ),
            )
        result = self.get(item_id)
        assert result is not None
        return result

    def update(
        self,
        item_id: str,
        *,
        status: str,
        last_action: str,
        error_code: str | None = None,
        candidate_id: str | None = None,
        evidence_refs: list[str] | None = None,
        review_status: str | None = None,
        knowledge_id: str | None = None,
        public_ref: str | None = None,
        increment_retry: bool = False,
    ) -> dict[str, Any]:
        if self.read_only:
            raise HardwareR1PromotionError("LEGACY_PROMOTION_READ_ONLY")
        current = self.get(item_id)
        if current is None:
            raise HardwareR1PromotionError("PROMOTION_NOT_FOUND")
        values = {
            "candidate_id": (
                candidate_id if candidate_id is not None else current["candidate_id"]
            ),
            "evidence_refs_json": json.dumps(
                evidence_refs if evidence_refs is not None else current["evidence_refs"],
                ensure_ascii=False,
            ),
            "review_status": (
                review_status if review_status is not None else current["review_status"]
            ),
            "knowledge_id": (
                knowledge_id if knowledge_id is not None else current["knowledge_id"]
            ),
            "public_ref": public_ref if public_ref is not None else current["public_ref"],
        }
        retry = current["retry_count"] + (1 if increment_retry else 0)
        now = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE hardware_r1_knowledge_promotion
                SET candidate_id=?,evidence_refs_json=?,status=?,last_action=?,
                    error_code=?,retry_count=?,review_status=?,knowledge_id=?,
                    public_ref=?,updated_at=?
                WHERE item_id=?
                """,
                (
                    values["candidate_id"],
                    values["evidence_refs_json"],
                    status,
                    last_action,
                    error_code,
                    retry,
                    values["review_status"],
                    values["knowledge_id"],
                    values["public_ref"],
                    now,
                    item_id,
                ),
            )
        result = self.get(item_id)
        assert result is not None
        return result


class HardwareR1KnowledgePromotionService:
    """Explicit, human-gated promotion of Workbench Golden Candidates."""

    def __init__(
        self,
        store: HardwareR1KnowledgePromotionStore,
        *,
        workbench_service: Any,
        bridge: HardwareR1GoldenKnowledgeBridge,
        candidate_repository: CandidateAssetRepository,
        operation_journal: HardwareAssetOperationJournal | None = None,
    ) -> None:
        self.store = store
        self.workbench = workbench_service
        self.bridge = bridge
        self.assets = candidate_repository
        self.operation_journal = operation_journal or HardwareAssetOperationJournal(
            candidate_repository.db_path
        )

    @staticmethod
    def _operation_id(
        asset_candidate_id: str,
        operation_type: str,
        revision: int,
        evidence_id: str | None = None,
    ) -> str:
        identity = "|".join(
            [asset_candidate_id, operation_type, str(int(revision)), evidence_id or ""]
        )
        return "HOP-R1-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]

    @staticmethod
    def _reconciliation_error(operation_type: str) -> str:
        return {
            "EVIDENCE_INTAKE": "EVIDENCE_INTAKE_RECONCILIATION_REQUIRED",
            "CANDIDATE_INTAKE": "CANDIDATE_INTAKE_RECONCILIATION_REQUIRED",
            "FORMAL_REVIEW": "FORMAL_REVIEW_RECONCILIATION_REQUIRED",
            "PUBLISH": "PUBLISH_RECONCILIATION_REQUIRED",
        }.get(str(operation_type).upper(), "REMOTE_OUTCOME_UNKNOWN")

    def _journal_transition(self, operation_id: str, state: str, **kwargs: Any) -> dict[str, Any]:
        try:
            return self.operation_journal.transition(operation_id, state, **kwargs)
        except HardwareAssetOperationJournalError as error:
            raise HardwareR1PromotionError(error.code) from error

    def _run_remote_operation(
        self,
        *,
        operation_type: str,
        asset_candidate_id: str | None,
        business_case_id: str,
        source_id: str,
        evidence_id: str | None,
        remote_idempotency_key: str | None,
        request_fingerprint: Mapping[str, Any],
        reconciliation: bool,
        action: Any,
        completed_response: Mapping[str, Any] | None = None,
        retry_failed: bool = False,
    ) -> dict[str, Any]:
        op_type = str(operation_type).strip().upper()
        asset_id = str(asset_candidate_id or "").strip()
        if not asset_id:
            raise HardwareR1PromotionError("CANDIDATE_NOT_FOUND")
        operation_id = self._operation_id(
            asset_id, op_type, PROMOTION_REVISION, evidence_id
        )
        try:
            entry = self.operation_journal.prepare(
                operation_id=operation_id,
                operation_type=op_type,
                business_case_id=business_case_id,
                candidate_id=asset_id,
                source_id=source_id,
                desired_action=f"HARDWARE_R1_{op_type}",
                request_fingerprint=request_fingerprint,
                remote_idempotency_key=remote_idempotency_key,
            )
        except HardwareAssetOperationJournalError as error:
            raise HardwareR1PromotionError(error.code) from error

        state = str(entry["operation_state"])
        prior_ambiguity = state in {"REMOTE_SENT", "OUTCOME_UNKNOWN", "RECONCILING"}
        controlled_replay_attempt = False
        if state == "COMPLETED":
            if completed_response is not None:
                return dict(completed_response)
            if op_type == "EVIDENCE_INTAKE":
                fp = entry["request_fingerprint"]
                return {
                    "evidence_id": evidence_id,
                    "source": {
                        "source_id": source_id,
                        "uri": fp.get("source_ref"),
                        "metadata": {"hardware_locator": fp.get("locator")},
                    },
                }
            if op_type == "CANDIDATE_INTAKE":
                fp = entry["request_fingerprint"]
                return {
                    "contract_version": "knowledge-candidate/v1",
                    "candidate_id": fp.get("candidate_id"),
                    "domain": "HARDWARE_CASE",
                    "object_type": "HARDWARE_CASE",
                    "structured_content": None,
                    "evidence_refs": fp.get("evidence_refs") or [],
                    "revision": PROMOTION_REVISION,
                }
            if op_type == "FORMAL_REVIEW":
                return {
                    "contract_version": "knowledge-review/v1",
                    "candidate_id": entry["request_fingerprint"].get("candidate_id"),
                    "review_status": "CONFIRMED",
                    "revision": PROMOTION_REVISION,
                }
            if op_type == "PUBLISH":
                try:
                    obj = self.bridge.adapter.resolve_publication(
                        str(entry["request_fingerprint"].get("candidate_id") or ""),
                        expected_revision=PROMOTION_REVISION,
                    )
                    return {"object": obj}
                except HardwareKnowledgeAdapterError as error:
                    raise HardwareR1PromotionError("PUBLISH_RECONCILIATION_REQUIRED") from error

        if state == "FAILED":
            entry = self._journal_transition(operation_id, "PREPARED")
            state = "PREPARED"
            prior_ambiguity = False

        if state == "REMOTE_SENT":
            entry = self._journal_transition(
                operation_id,
                "OUTCOME_UNKNOWN",
                error_code="REMOTE_OUTCOME_UNKNOWN",
                recovery_action=(
                    entry.get("recovery_action")
                    or "REMOTE_RESULT_NOT_COMMITTED_LOCALLY"
                ),
            )
            state = "OUTCOME_UNKNOWN"
            prior_ambiguity = True

        if state in {"OUTCOME_UNKNOWN", "RECONCILING"}:
            if not reconciliation:
                raise HardwareR1PromotionError(self._reconciliation_error(op_type))
            if op_type == "EVIDENCE_INTAKE":
                prior = self.operation_journal.get(operation_id) or {}
                self._journal_transition(
                    operation_id,
                    "RECONCILING",
                    error_code="REMOTE_OUTCOME_UNKNOWN",
                    recovery_action="EVIDENCE_QUERY_PENDING",
                )
                try:
                    resolved = self.bridge.adapter.resolve_evidence(str(evidence_id or ""))
                except HardwareKnowledgeAdapterError as error:
                    if error.status_code == 404 or error.code in {
                        "EVIDENCE_NOT_FOUND", "KNOWLEDGE_EVIDENCE_NOT_FOUND"
                    }:
                        if "CONTROLLED_REPLAY" in str(prior.get("recovery_action") or ""):
                            self._journal_transition(
                                operation_id, "OUTCOME_UNKNOWN",
                                error_code="REMOTE_OUTCOME_UNKNOWN",
                                recovery_action="EVIDENCE_REPLAY_OUTCOME_UNKNOWN",
                            )
                            raise HardwareR1PromotionError(
                                "EVIDENCE_INTAKE_RECONCILIATION_REQUIRED"
                            ) from error
                        self._journal_transition(
                            operation_id, "REMOTE_SENT",
                            error_code="REMOTE_OUTCOME_UNKNOWN",
                            recovery_action="EVIDENCE_CONTROLLED_REPLAY_SENT",
                        )
                        controlled_replay_attempt = True
                    else:
                        self._journal_transition(
                            operation_id, "OUTCOME_UNKNOWN",
                            error_code="REMOTE_OUTCOME_UNKNOWN",
                            recovery_action="EVIDENCE_QUERY_FAILED",
                        )
                        raise HardwareR1PromotionError(
                            "EVIDENCE_INTAKE_RECONCILIATION_REQUIRED"
                        ) from error
                else:
                    source = resolved.get("source")
                    metadata = source.get("metadata") if isinstance(source, Mapping) else None
                    matches = (
                        resolved.get("evidence_id") == evidence_id
                        and isinstance(source, Mapping)
                        and str(source.get("source_id") or "") == source_id
                        and str(source.get("uri") or "")
                        == str(entry["request_fingerprint"].get("source_ref") or "")
                        and isinstance(metadata, Mapping)
                        and metadata.get("hardware_locator")
                        == entry["request_fingerprint"].get("locator")
                        and hashlib.sha256(
                            str(resolved.get("excerpt") or "").encode("utf-8")
                        ).hexdigest()
                        == entry["request_fingerprint"].get("excerpt_sha256")
                    )
                    if not matches:
                        self._journal_transition(
                            operation_id, "OUTCOME_UNKNOWN",
                            error_code="ASSET_SCOPED_AMBIGUITY",
                            recovery_action="EVIDENCE_IDENTITY_CONFLICT",
                        )
                        raise HardwareR1PromotionError("ASSET_SCOPED_AMBIGUITY")
                    self._journal_transition(operation_id, "COMPLETED")
                    return resolved
            elif op_type == "CANDIDATE_INTAKE":
                prior = self.operation_journal.get(operation_id) or {}
                if "CONTROLLED_REPLAY" in str(prior.get("recovery_action") or ""):
                    self._journal_transition(
                        operation_id, "OUTCOME_UNKNOWN",
                        error_code="REMOTE_OUTCOME_UNKNOWN",
                        recovery_action="CANDIDATE_REPLAY_OUTCOME_UNKNOWN",
                    )
                    raise HardwareR1PromotionError(
                        "CANDIDATE_INTAKE_RECONCILIATION_REQUIRED"
                    )
                self._journal_transition(
                    operation_id, "RECONCILING",
                    error_code="REMOTE_OUTCOME_UNKNOWN",
                    recovery_action="CANDIDATE_CONTROLLED_REPLAY_PENDING",
                )
                self._journal_transition(
                    operation_id, "REMOTE_SENT",
                    error_code="REMOTE_OUTCOME_UNKNOWN",
                    recovery_action="CANDIDATE_CONTROLLED_REPLAY_SENT",
                )
                controlled_replay_attempt = True
            else:
                self._journal_transition(
                    operation_id, "OUTCOME_UNKNOWN",
                    error_code="REMOTE_OUTCOME_UNKNOWN",
                    recovery_action="RECONCILIATION_REQUIRED_NO_QUERY_CONTRACT",
                )
                raise HardwareR1PromotionError(self._reconciliation_error(op_type))

        if state == "PREPARED":
            self._journal_transition(operation_id, "REMOTE_SENT")
        elif op_type == "EVIDENCE_INTAKE" and reconciliation and state in {
            "OUTCOME_UNKNOWN", "RECONCILING"
        }:
            # The explicit not-found branch above has authorized exactly one replay.
            latest = self.operation_journal.get(operation_id) or {}
            if latest.get("operation_state") != "REMOTE_SENT":
                raise HardwareR1PromotionError(
                    "EVIDENCE_INTAKE_RECONCILIATION_REQUIRED"
                )

        try:
            response = action()
        except HardwareKnowledgeAdapterError as error:
            status = error.status_code
            if op_type == "EVIDENCE_INTAKE" and status == 409:
                self._journal_transition(
                    operation_id, "OUTCOME_UNKNOWN",
                    error_code="ASSET_SCOPED_AMBIGUITY",
                    recovery_action="EVIDENCE_ID_CONFLICT",
                )
                raise HardwareR1PromotionError("ASSET_SCOPED_AMBIGUITY") from error
            local_rejection = error.code in {
                "REVISION_INVALID", "EVIDENCE_CONTRACT_INVALID",
                "CANDIDATE_CONTRACT_INVALID", "REVIEW_CONTRACT_INVALID",
                "PUBLISH_CONTRACT_INVALID", "HARDWARE_PUBLISH_GATE_NOT_PASSED",
                "EVIDENCE_MISSING",
            }
            ambiguous_http_status = status in {408, 425, 429}
            definite_rejection = (
                not prior_ambiguity
                and not ambiguous_http_status
                and (local_rejection or (status is not None and 400 <= status < 500 and status != 409))
            )
            if definite_rejection:
                self._journal_transition(
                    operation_id, "FAILED", error_code=error.code,
                    recovery_action="REMOTE_BUSINESS_REJECTION",
                )
                raise
            self._journal_transition(
                operation_id, "OUTCOME_UNKNOWN",
                error_code="REMOTE_OUTCOME_UNKNOWN",
                recovery_action=(
                    f"{op_type}_CONTROLLED_REPLAY_OUTCOME_UNKNOWN"
                    if controlled_replay_attempt
                    else "REMOTE_RESPONSE_UNVERIFIED"
                ),
            )
            raise HardwareR1PromotionError(self._reconciliation_error(op_type)) from error
        except Exception as error:
            self._journal_transition(
                operation_id, "OUTCOME_UNKNOWN",
                error_code="REMOTE_OUTCOME_UNKNOWN",
                recovery_action=(
                    f"{op_type}_CONTROLLED_REPLAY_OUTCOME_UNKNOWN"
                    if controlled_replay_attempt
                    else "REMOTE_TRANSPORT_OR_RESPONSE_FAILURE"
                ),
            )
            raise HardwareR1PromotionError(self._reconciliation_error(op_type)) from error

        if op_type == "PUBLISH":
            self._journal_transition(
                operation_id, "REMOTE_SENT",
                recovery_action="REMOTE_RESPONSE_VALIDATED_LOCAL_COMMIT_PENDING",
            )
            return response
        self._journal_transition(operation_id, "COMPLETED")
        return response

    def _context(self, item_id: str) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
        try:
            item = self.workbench.get_item(item_id)
        except Exception as error:
            code = str(getattr(error, "code", None) or "BATCH_ITEM_NOT_FOUND")
            raise HardwareR1PromotionError(code) from error
        if item.get("result") not in {"CANDIDATE_READY", "REVIEW"}:
            raise HardwareR1PromotionError("GOLDEN_CANDIDATE_NOT_READY")
        candidate_id = str(item.get("candidate_id") or "").strip()
        if not candidate_id:
            raise HardwareR1PromotionError("CANDIDATE_NOT_FOUND")
        try:
            asset = self.assets.get_candidate(candidate_id)
        except CandidateAssetRepositoryError as error:
            raise HardwareR1PromotionError(error.code) from error
        if not isinstance(asset, dict):
            raise HardwareR1PromotionError("CANDIDATE_NOT_FOUND")
        if asset.get("asset_status") != "ACTIVE":
            raise HardwareR1PromotionError("CANDIDATE_ASSET_INVALIDATED")
        if str(asset.get("business_case_id")) != str(item.get("business_case_id")) or str(
            asset.get("source_id")
        ) != str(item.get("source_id")):
            raise HardwareR1PromotionError("CANDIDATE_DATA_INTEGRITY_ERROR")
        validation = item.get("evidence_validation")
        if not isinstance(validation, Mapping):
            raise HardwareR1PromotionError("EVIDENCE_GATE_RESULT_REQUIRED")
        evidence_ids = [
            str(value.get("evidence_id") or "")
            for value in asset.get("evidence_refs") or []
            if isinstance(value, Mapping)
        ]
        if not evidence_ids or any(not value for value in evidence_ids):
            raise HardwareR1PromotionError("CANDIDATE_DATA_INTEGRITY_ERROR")
        return item, asset, evidence_ids

    @staticmethod
    def _promotion_view(record: Mapping[str, Any], evidence_ids: list[str]) -> dict[str, Any]:
        return {
            "contract_version": PROMOTION_CONTRACT_VERSION,
            "asset_candidate_id": record["asset_candidate_id"],
            "knowledge_candidate_id": record.get("knowledge_candidate_id"),
            "business_case_id": record.get("business_case_id"),
            "source_id": record.get("source_id"),
            "knowledge_id": record.get("knowledge_id"),
            "public_ref": record.get("public_ref"),
            "evidence_refs": list(evidence_ids),
            "status": record["promotion_status"],
            "last_action": record.get("last_action"),
            "error_code": record.get("error_code"),
            "retry_count": int(record.get("retry_count") or 0),
            "formal_review_status": record.get("formal_review_status"),
            "origin_batch_id": record.get("origin_batch_id"),
            "origin_item_id": record.get("origin_item_id"),
            "idempotent_reuse": False,
            "auto_publish": False,
        }

    def _record(self, item: Mapping[str, Any], asset: Mapping[str, Any]) -> dict[str, Any] | None:
        try:
            record = self.assets.get_promotion_record(str(asset["candidate_id"]))
        except CandidateAssetRepositoryError as error:
            raise HardwareR1PromotionError(error.code) from error
        legacy = self.store.get(str(item["item_id"]))
        if legacy is not None:
            record = self._migrate_legacy(item, asset, legacy, record)
        return record

    def _migrate_legacy(
        self,
        item: Mapping[str, Any],
        asset: Mapping[str, Any],
        legacy: Mapping[str, Any],
        record: dict[str, Any] | None,
    ) -> dict[str, Any]:
        asset_id = str(asset["candidate_id"])
        if (
            str(legacy.get("business_case_id")) != str(asset.get("business_case_id"))
            or str(legacy.get("source_id")) != str(asset.get("source_id"))
            or str(legacy.get("golden_hash")) != _json_hash(asset["knowledge_object"])
        ):
            raise HardwareR1PromotionError("PROMOTION_IDEMPOTENCY_CONFLICT")
        legacy_evidence = [str(value) for value in legacy.get("evidence_refs") or []]
        durable_evidence = sorted(
            str(value.get("evidence_id") or "")
            for value in asset.get("evidence_refs") or []
            if isinstance(value, Mapping)
        )
        if legacy_evidence and sorted(legacy_evidence) != durable_evidence:
            raise HardwareR1PromotionError("PROMOTION_IDEMPOTENCY_CONFLICT")
        legacy_status = str(legacy.get("status") or "")
        metadata = {
            "knowledge_candidate_id": legacy.get("candidate_id"),
            "knowledge_id": legacy.get("knowledge_id"),
            "public_ref": legacy.get("public_ref"),
            "formal_review_status": legacy.get("review_status"),
            "origin_batch_id": legacy.get("batch_id"),
            "origin_item_id": legacy.get("item_id"),
            "retry_count": int(legacy.get("retry_count") or 0),
        }
        if record is not None:
            for key, value in metadata.items():
                if value not in (None, "") and record.get(key) != value:
                    raise HardwareR1PromotionError("PROMOTION_IDEMPOTENCY_CONFLICT")
            if record["promotion_status"] != legacy_status:
                raise HardwareR1PromotionError("PROMOTION_IDEMPOTENCY_CONFLICT")
            return record
        if str(asset.get("promotion_status")) != "NOT_STARTED":
            raise HardwareR1PromotionError("CANDIDATE_DATA_INTEGRITY_ERROR")
        try:
            committed = self.assets.update_promotion_status(
                asset_id,
                expected_status="NOT_STARTED",
                new_status=legacy_status,
                expected_row_version=int(asset["row_version"]),
                actor="C1_LEGACY_PROMOTION_MIGRATION",
                reason="Deterministic migration from legacy Workbench promotion ledger",
                **metadata,
            )
            return dict(committed["promotion_record"])
        except (CandidateAssetRepositoryError, KeyError, TypeError, ValueError) as error:
            code = str(getattr(error, "code", None) or "CANDIDATE_DATA_INTEGRITY_ERROR")
            raise HardwareR1PromotionError(code) from error

    def _transition(
        self,
        item: Mapping[str, Any],
        asset: Mapping[str, Any],
        record: Mapping[str, Any],
        target: str,
        *,
        action: str,
        retry: bool = False,
        error_code: str | None = None,
        **metadata: Any,
    ) -> dict[str, Any]:
        try:
            committed = self.assets.update_promotion_status(
                str(asset["candidate_id"]),
                expected_status=str(record["promotion_status"]),
                new_status=target,
                expected_row_version=int(asset["row_version"]),
                actor="HARDWARE_R1_PROMOTION",
                reason=f"Promotion {action.lower()} transition",
                error_code=error_code,
                retry_count=int(record.get("retry_count") or 0) + int(retry),
                origin_batch_id=str(item.get("batch_id") or item.get("origin_batch_id") or ""),
                origin_item_id=str(item.get("item_id") or ""),
                **metadata,
            )
            return dict(committed["promotion_record"])
        except CandidateAssetRepositoryError as error:
            raise HardwareR1PromotionError(error.code) from error

    # Canonical Durable Asset-backed orchestration.
    # Keep exactly one implementation of each public promotion operation.

    def precheck_item(self, item_id: str) -> dict[str, Any]:
        item, asset, evidence_ids = self._context(item_id)
        review_status = str(asset.get("production_review_status") or "")
        if review_status == "REQUIRED":
            raise HardwareR1PromotionError("CANDIDATE_LOCKED_BY_REVIEW")
        if review_status not in {"NOT_REQUIRED", "RESOLVED"}:
            raise HardwareR1PromotionError("CANDIDATE_DATA_INTEGRITY_ERROR")
        try:
            check = self.bridge.precheck(asset["knowledge_object"], item["evidence_validation"])
        except HardwareR1GoldenBridgeError as error:
            raise HardwareR1PromotionError(error.code) from error
        record = self._record(item, asset)
        if record is None:
            if asset.get("promotion_status") != "NOT_STARTED":
                raise HardwareR1PromotionError("CANDIDATE_DATA_INTEGRITY_ERROR")
            try:
                updated = self.assets.update_promotion_status(
                    str(asset["candidate_id"]), expected_status="NOT_STARTED",
                    new_status="PRECHECK_PASS", expected_row_version=int(asset["row_version"]),
                    actor="HARDWARE_R1_PROMOTION",
                    reason="Durable Candidate passed R1 Golden precheck",
                    origin_batch_id=str(item.get("batch_id") or ""),
                    origin_item_id=str(item.get("item_id") or ""),
                )
                record = dict(updated["promotion_record"])
            except CandidateAssetRepositoryError as error:
                raise HardwareR1PromotionError(error.code) from error
        return {"contract_version": PROMOTION_CONTRACT_VERSION, "precheck": check,
                "promotion": self._promotion_view(record, evidence_ids)}

    def intake_item(
        self, item_id: str, *, retry: bool = False, reconciliation: bool = False
    ) -> dict[str, Any]:
        self.precheck_item(item_id)
        item, asset, evidence_ids = self._context(item_id)
        record = self._record(item, asset)
        if record is None:
            raise HardwareR1PromotionError("PROMOTION_NOT_FOUND")
        if record["promotion_status"] in {"CANDIDATE_INTAKED", "REVIEW_CONFIRMED",
                "PUBLISHED_PENDING_QUERY_BACK", "VERIFIED"}:
            return {**self._promotion_view(record, evidence_ids), "idempotent_reuse": True}
        if record["promotion_status"] not in {"PRECHECK_PASS", "INTAKE_FAILED"}:
            raise HardwareR1PromotionError("PROMOTION_STATE_INVALID")
        try:
            result = self.bridge.intake(
                asset["knowledge_object"],
                item["evidence_validation"],
                asset_candidate_id=str(asset["candidate_id"]),
                candidate_created_at=str(asset.get("created_at") or "") or None,
                reconciliation=reconciliation,
                operation_runner=lambda **operation: self._run_remote_operation(
                    **operation, retry_failed=retry
                ),
            )
            knowledge_candidate_id = str(result["candidate"]["candidate_id"])
            returned_ids = [str(value) for value in result["candidate"].get("evidence_refs") or []]
            if sorted(returned_ids) != sorted(evidence_ids):
                raise HardwareR1PromotionError("CANDIDATE_DATA_INTEGRITY_ERROR")
        except HardwareR1GoldenBridgeError as error:
            failed = self._transition(item, asset, record, "INTAKE_FAILED", action="INTAKE",
                                      retry=retry, error_code=error.code)
            return {**self._promotion_view(failed, evidence_ids), "idempotent_reuse": False}
        committed = self._transition(item, asset, record, "CANDIDATE_INTAKED", action="INTAKE",
                                     retry=retry, knowledge_candidate_id=knowledge_candidate_id)
        return {**self._promotion_view(committed, evidence_ids), "idempotent_reuse": False,
                "intake_status": result["status"]}

    def review_item(
        self, item_id: str, *, reviewer: str,
        review_time: datetime, review_comment: str | None = None,
    ) -> dict[str, Any]:
        item, asset, evidence_ids = self._context(item_id)
        record = self._record(item, asset)
        if record is None or record["promotion_status"] == "PRECHECK_PASS":
            self.intake_item(item_id)
            item, asset, evidence_ids = self._context(item_id)
            record = self._record(item, asset)
        if record is None or record["promotion_status"] != "CANDIDATE_INTAKED":
            if record and record["promotion_status"] == "REVIEW_CONFIRMED":
                return {**self._promotion_view(record, evidence_ids), "idempotent_reuse": True}
            raise HardwareR1PromotionError("PROMOTION_STATE_INVALID")
        try:
            response = self.bridge.review(
                candidate_id=str(record["knowledge_candidate_id"]),
                original_golden=asset["knowledge_object"],
                # Formal Review is an approval step, not a second content editor.
                # The already reviewed Durable Candidate is the only content
                # allowed to cross into Unified Knowledge.
                confirmed_content=asset["knowledge_object"],
                reviewer=reviewer,
                review_time=review_time,
                review_comment=review_comment,
                asset_candidate_id=str(asset["candidate_id"]),
                business_case_id=str(asset["business_case_id"]),
                source_id=str(asset["source_id"]),
                operation_runner=lambda **operation: self._run_remote_operation(
                    **operation
                ),
            )
        except HardwareR1GoldenBridgeError as error:
            failed = self._transition(item, asset, record, "REVIEW_FAILED", action="REVIEW",
                                      error_code=error.code)
            return {**self._promotion_view(failed, evidence_ids), "idempotent_reuse": False}
        committed = self._transition(
            item, asset, record, "REVIEW_CONFIRMED", action="REVIEW",
            formal_review_status=str(response.get("review_status") or "CONFIRMED"),
        )
        return {**self._promotion_view(committed, evidence_ids), "idempotent_reuse": False}

    def publish_item(
        self, item_id: str, *, publisher: str, published_at: datetime, retry: bool = False,
    ) -> dict[str, Any]:
        item, asset, evidence_ids = self._context(item_id)
        record = self._record(item, asset)
        if record is None:
            raise HardwareR1PromotionError("PROMOTION_NOT_FOUND")
        self._raise_for_pending_operation(str(asset["candidate_id"]))
        state = record["promotion_status"]
        if state in {"VERIFIED", "PUBLISHED_PENDING_QUERY_BACK"}:
            return {**self._promotion_view(record, evidence_ids), "idempotent_reuse": True}
        if state not in {"REVIEW_CONFIRMED", "PUBLISH_FAILED"}:
            raise HardwareR1PromotionError("HUMAN_REVIEW_REQUIRED")
        try:
            result = self.bridge.publish(
                business_case_id=str(asset["business_case_id"]),
                candidate_id=str(record["knowledge_candidate_id"]),
                evidence_refs=evidence_ids, publisher=publisher, published_at=published_at,
                asset_candidate_id=str(asset["candidate_id"]),
                source_id=str(asset["source_id"]),
                reconciliation=False,
                record_source_reference=False,
                operation_runner=lambda **operation: self._run_remote_operation(
                    **operation, retry_failed=retry
                ),
            )
            obj = result.get("object")
            if not isinstance(obj, Mapping) or not obj.get("knowledge_id"):
                raise HardwareR1PromotionError("KNOWLEDGE_OBJECT_ID_MISSING")
        except HardwareR1GoldenBridgeError as error:
            operation_id = self._operation_id(
                str(asset["candidate_id"]), "PUBLISH", PROMOTION_REVISION
            )
            try:
                operation = self.operation_journal.get(operation_id)
            except HardwareAssetOperationJournalError as journal_error:
                raise HardwareR1PromotionError(journal_error.code) from journal_error
            if operation and operation.get("operation_state") in {
                "PREPARED", "REMOTE_SENT", "OUTCOME_UNKNOWN", "RECONCILING"
            }:
                if operation.get("operation_state") == "REMOTE_SENT":
                    self._journal_transition(
                        operation_id, "OUTCOME_UNKNOWN",
                        error_code="REMOTE_OUTCOME_UNKNOWN",
                        recovery_action="REMOTE_RESPONSE_UNVERIFIED",
                    )
                raise HardwareR1PromotionError(
                    "PUBLISH_RECONCILIATION_REQUIRED"
                ) from error
            failed = self._transition(item, asset, record, "PUBLISH_FAILED", action="PUBLISH",
                                      retry=retry, error_code=error.code)
            return {**self._promotion_view(failed, evidence_ids), "idempotent_reuse": False}
        committed = self._transition(
            item, asset, record, "PUBLISHED_PENDING_QUERY_BACK", action="PUBLISH",
            retry=retry, knowledge_id=str(obj["knowledge_id"]),
            public_ref=str(result.get("public_ref") or record["knowledge_candidate_id"]),
        )
        try:
            self.bridge.add_source_reference(
                str(asset["business_case_id"]),
                str(asset["source_id"]),
                str(obj["knowledge_id"]),
            )
        except HardwareR1GoldenBridgeError as error:
            raise HardwareR1PromotionError("PUBLISH_RECONCILIATION_REQUIRED") from error
        operation_id = self._operation_id(
            str(asset["candidate_id"]), "PUBLISH", PROMOTION_REVISION
        )
        self._journal_transition(operation_id, "COMPLETED")
        return {**self._promotion_view(committed, evidence_ids), "idempotent_reuse": False}

    def _pending_operations(self, asset_candidate_id: str) -> list[dict[str, Any]]:
        try:
            return [
                item for item in self.operation_journal.list_nonterminal()
                if str(item.get("candidate_id") or "") == str(asset_candidate_id)
            ]
        except HardwareAssetOperationJournalError as error:
            raise HardwareR1PromotionError(error.code) from error

    def _raise_for_pending_operation(self, asset_candidate_id: str) -> None:
        pending = self._pending_operations(asset_candidate_id)
        if pending:
            raise HardwareR1PromotionError(
                self._reconciliation_error(str(pending[0].get("operation_type") or ""))
            )

    def recovery_diagnostics(self) -> dict[str, Any]:
        try:
            pending = [
                item for item in self.operation_journal.list_nonterminal()
                if item.get("operation_type") in {
                    "EVIDENCE_INTAKE", "CANDIDATE_INTAKE", "FORMAL_REVIEW", "PUBLISH"
                }
            ]
        except HardwareAssetOperationJournalError as error:
            raise HardwareR1PromotionError(error.code) from error
        blocked = {
            str(item.get("candidate_id") or item.get("business_case_id") or item["operation_id"])
            for item in pending
        }
        last_error = next(
            (str(item.get("error_code")) for item in reversed(pending) if item.get("error_code")),
            self._reconciliation_error(str(pending[-1]["operation_type"])) if pending else None,
        )
        return {
            "pending_remote_reconciliation_count": len(pending),
            "blocked_asset_count": len(blocked),
            "last_recovery_error": last_error,
            "recovery_status": "DEGRADED" if pending else "COMPLETED",
        }

    def _reconcile_evidence_entry(
        self, entry: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        operation_id = str(entry["operation_id"])
        state = str(entry.get("operation_state") or "")
        if state == "REMOTE_SENT":
            entry = self._journal_transition(
                operation_id, "OUTCOME_UNKNOWN",
                error_code="REMOTE_OUTCOME_UNKNOWN",
                recovery_action=entry.get("recovery_action") or "REMOTE_RESULT_NOT_COMMITTED_LOCALLY",
            )
        self._journal_transition(
            operation_id, "RECONCILING",
            error_code="REMOTE_OUTCOME_UNKNOWN",
            recovery_action="EVIDENCE_QUERY_PENDING",
        )
        fp = entry.get("request_fingerprint") or {}
        evidence_id = str(entry.get("source_id") or "")
        # Evidence id is not a journal column; it is part of the operation identity
        # and fingerprint. Resolve it from the persisted request fingerprint.
        evidence_id = str(fp.get("evidence_id") or "")
        try:
            resolved = self.bridge.adapter.resolve_evidence(evidence_id)
        except HardwareKnowledgeAdapterError as error:
            if error.status_code == 404 or error.code in {
                "EVIDENCE_NOT_FOUND", "KNOWLEDGE_EVIDENCE_NOT_FOUND"
            }:
                self._journal_transition(
                    operation_id, "OUTCOME_UNKNOWN",
                    error_code="REMOTE_OUTCOME_UNKNOWN",
                    recovery_action="EVIDENCE_QUERY_NOT_FOUND",
                )
                return None
            self._journal_transition(
                operation_id, "OUTCOME_UNKNOWN",
                error_code="REMOTE_OUTCOME_UNKNOWN",
                recovery_action="EVIDENCE_QUERY_FAILED",
            )
            return None
        source = resolved.get("source")
        metadata = source.get("metadata") if isinstance(source, Mapping) else None
        matches = (
            resolved.get("evidence_id") == evidence_id
            and isinstance(source, Mapping)
            and str(source.get("source_id") or "") == str(entry.get("source_id") or "")
            and str(source.get("uri") or "") == str(fp.get("source_ref") or "")
            and isinstance(metadata, Mapping)
            and metadata.get("hardware_locator") == fp.get("locator")
            and hashlib.sha256(
                str(resolved.get("excerpt") or "").encode("utf-8")
            ).hexdigest() == fp.get("excerpt_sha256")
        )
        if not matches:
            self._journal_transition(
                operation_id, "OUTCOME_UNKNOWN",
                error_code="ASSET_SCOPED_AMBIGUITY",
                recovery_action="EVIDENCE_IDENTITY_CONFLICT",
            )
            raise HardwareR1PromotionError("ASSET_SCOPED_AMBIGUITY")
        self._journal_transition(operation_id, "COMPLETED")
        return resolved

    def _reconcile_publish(
        self,
        item: Mapping[str, Any],
        asset: Mapping[str, Any],
        evidence_ids: list[str],
        record: Mapping[str, Any],
        entry: Mapping[str, Any],
    ) -> dict[str, Any]:
        operation_id = str(entry["operation_id"])
        if str(entry.get("operation_state")) == "REMOTE_SENT":
            entry = self._journal_transition(
                operation_id, "OUTCOME_UNKNOWN",
                error_code="REMOTE_OUTCOME_UNKNOWN",
                recovery_action=entry.get("recovery_action") or "REMOTE_RESULT_NOT_COMMITTED_LOCALLY",
            )
        self._journal_transition(
            operation_id, "RECONCILING",
            error_code="REMOTE_OUTCOME_UNKNOWN",
            recovery_action="PUBLICATION_QUERY_PENDING",
        )
        knowledge_candidate_id = str(record.get("knowledge_candidate_id") or "")
        if not knowledge_candidate_id:
            raise HardwareR1PromotionError("CANDIDATE_DATA_INTEGRITY_ERROR")
        try:
            published = self.bridge.adapter.resolve_publication(
                knowledge_candidate_id,
                expected_revision=PROMOTION_REVISION,
            )
        except HardwareKnowledgeAdapterError as error:
            self._journal_transition(
                operation_id, "OUTCOME_UNKNOWN",
                error_code="REMOTE_OUTCOME_UNKNOWN",
                recovery_action=(
                    "PUBLICATION_QUERY_NOT_FOUND"
                    if error.status_code == 404 or error.code == "KNOWLEDGE_PUBLIC_REF_NOT_FOUND"
                    else "PUBLICATION_QUERY_FAILED"
                ),
            )
            raise HardwareR1PromotionError("PUBLISH_RECONCILIATION_REQUIRED") from error
        knowledge_id = str(published.get("knowledge_id") or "")
        valid = (
            published.get("candidate_ref") == knowledge_candidate_id
            and int(published.get("revision") or 0) == PROMOTION_REVISION
            and list(published.get("evidence_refs") or []) == evidence_ids
            and published.get("domain") == "HARDWARE_CASE"
            and published.get("object_type") == "HARDWARE_CASE"
            and bool(knowledge_id)
        )
        if not valid:
            self._journal_transition(
                operation_id, "OUTCOME_UNKNOWN",
                error_code="CANDIDATE_DATA_INTEGRITY_ERROR",
                recovery_action="PUBLICATION_METADATA_MISMATCH",
            )
            raise HardwareR1PromotionError("CANDIDATE_DATA_INTEGRITY_ERROR")

        current_status = str(record.get("promotion_status") or "")
        if current_status in {"REVIEW_CONFIRMED", "PUBLISH_FAILED"}:
            repaired = self._transition(
                item, asset, record, "PUBLISHED_PENDING_QUERY_BACK",
                action="PUBLISH_RECOVERY", knowledge_id=knowledge_id,
                public_ref=knowledge_candidate_id,
            )
        elif current_status in {"PUBLISHED_PENDING_QUERY_BACK", "VERIFIED"}:
            if str(record.get("knowledge_id") or "") != knowledge_id:
                raise HardwareR1PromotionError("CANDIDATE_DATA_INTEGRITY_ERROR")
            repaired = dict(record)
        else:
            raise HardwareR1PromotionError("CANDIDATE_DATA_INTEGRITY_ERROR")
        try:
            self.bridge.add_source_reference(
                str(asset["business_case_id"]), str(asset["source_id"]), knowledge_id
            )
        except HardwareR1GoldenBridgeError as error:
            self._journal_transition(
                operation_id, "OUTCOME_UNKNOWN",
                error_code="REMOTE_OUTCOME_UNKNOWN",
                recovery_action="FORMAL_SOURCE_REFERENCE_REPAIR_PENDING",
            )
            raise HardwareR1PromotionError("PUBLISH_RECONCILIATION_REQUIRED") from error
        self._journal_transition(operation_id, "COMPLETED")
        return {
            **self._promotion_view(repaired, evidence_ids),
            "idempotent_reuse": False,
            "reconciled": True,
        }

    def reconcile_item(self, item_id: str) -> dict[str, Any]:
        item, asset, evidence_ids = self._context(item_id)
        record = self._record(item, asset)
        if record is None:
            raise HardwareR1PromotionError("PROMOTION_NOT_FOUND")
        try:
            pending = [
                entry for entry in self.operation_journal.list_nonterminal()
                if str(entry.get("candidate_id") or "") == str(asset["candidate_id"])
            ]
        except HardwareAssetOperationJournalError as error:
            raise HardwareR1PromotionError(error.code) from error
        if not pending:
            completed = self.operation_journal.list_operations(
                candidate_id=str(asset["candidate_id"]),
                operation_type="CANDIDATE_INTAKE",
                states={"COMPLETED"},
            )
            if record.get("promotion_status") == "PRECHECK_PASS" and completed:
                result = self.intake_item(item_id, reconciliation=True)
                return {**result, "reconciled": True}
            return {**self._promotion_view(record, evidence_ids), "reconciled": False}

        entry = pending[0]
        operation_type = str(entry.get("operation_type") or "")
        if operation_type in {"EVIDENCE_INTAKE", "CANDIDATE_INTAKE"}:
            if record.get("promotion_status") not in {"PRECHECK_PASS", "INTAKE_FAILED"}:
                raise HardwareR1PromotionError("CANDIDATE_DATA_INTEGRITY_ERROR")
            result = self.intake_item(item_id, retry=True, reconciliation=True)
            return {**result, "reconciled": True}
        if operation_type == "FORMAL_REVIEW":
            if entry.get("operation_state") == "REMOTE_SENT":
                self._journal_transition(
                    str(entry["operation_id"]), "OUTCOME_UNKNOWN",
                    error_code="REMOTE_OUTCOME_UNKNOWN",
                    recovery_action=entry.get("recovery_action") or "REMOTE_RESULT_NOT_COMMITTED_LOCALLY",
                )
            raise HardwareR1PromotionError("FORMAL_REVIEW_RECONCILIATION_REQUIRED")
        if operation_type == "PUBLISH":
            return self._reconcile_publish(item, asset, evidence_ids, record, entry)
        raise HardwareR1PromotionError("CANDIDATE_DATA_INTEGRITY_ERROR")

    def reconcile_startup(self, *, max_remote_queries: int = 2) -> dict[str, Any]:
        """Use a bounded read-only reconciliation budget during app startup."""
        budget = max(0, min(int(max_remote_queries), 4))
        try:
            entries = self.operation_journal.list_nonterminal()
        except HardwareAssetOperationJournalError as error:
            raise HardwareR1PromotionError(error.code) from error
        attempts = 0
        for entry in entries:
            if attempts >= budget:
                break
            if entry.get("operation_state") not in {
                "REMOTE_SENT", "OUTCOME_UNKNOWN", "RECONCILING"
            }:
                continue
            op_type = str(entry.get("operation_type") or "")
            if op_type not in {"EVIDENCE_INTAKE", "PUBLISH"}:
                continue
            attempts += 1
            try:
                if op_type == "EVIDENCE_INTAKE":
                    self._reconcile_evidence_entry(entry)
                    continue
                asset_id = str(entry.get("candidate_id") or "")
                asset = self.assets.get_candidate(asset_id)
                promotion_record = self.assets.get_promotion_record(asset_id)
                if not asset or not promotion_record:
                    self._journal_transition(
                        str(entry["operation_id"]), "OUTCOME_UNKNOWN",
                        error_code="CANDIDATE_DATA_INTEGRITY_ERROR",
                        recovery_action="PROMOTION_RECORD_MISSING",
                    )
                    continue
                item_id = str(promotion_record.get("origin_item_id") or "")
                item = self._workbench_item(item_id)
                evidence_ids = [
                    str(value.get("evidence_id") or "")
                    for value in asset.get("evidence_refs") or []
                    if isinstance(value, Mapping)
                ]
                self._reconcile_publish(
                    item, asset, evidence_ids, promotion_record, entry
                )
            except (HardwareR1PromotionError, CandidateAssetRepositoryError):
                # Ambiguity remains asset-scoped and is reflected in diagnostics.
                continue
        return {**self.recovery_diagnostics(), "startup_queries_used": attempts}

    def verify_item(self, item_id: str, *, retry: bool = False) -> dict[str, Any]:
        item, asset, evidence_ids = self._context(item_id)
        record = self._record(item, asset)
        if record is None:
            raise HardwareR1PromotionError("PROMOTION_NOT_FOUND")
        self._raise_for_pending_operation(str(asset["candidate_id"]))
        state = record["promotion_status"]
        if state == "VERIFIED":
            return {**self._promotion_view(record, evidence_ids), "idempotent_reuse": True}
        if state not in {"PUBLISHED_PENDING_QUERY_BACK", "VERIFY_FAILED"}:
            raise HardwareR1PromotionError("PROMOTION_STATE_INVALID")
        try:
            verified = self.bridge.query_back(str(asset["business_case_id"]))
            obj = verified.get("object")
            if not isinstance(obj, Mapping):
                raise HardwareR1PromotionError("KNOWLEDGE_RESPONSE_INVALID")
            if str(obj.get("knowledge_id") or "") != str(record.get("knowledge_id") or ""):
                raise HardwareR1PromotionError("QUERY_BACK_OBJECT_MISMATCH")
            if list(obj.get("evidence_refs") or []) != evidence_ids:
                raise HardwareR1PromotionError("QUERY_BACK_EVIDENCE_MISMATCH")
            source = verified.get("source")
            if not isinstance(source, Mapping) or str(source.get("source_id") or "") != str(asset["source_id"]):
                raise HardwareR1PromotionError("QUERY_BACK_SOURCE_MISMATCH")
        except HardwareR1GoldenBridgeError as error:
            failed = self._transition(item, asset, record, "VERIFY_FAILED", action="VERIFY",
                                      retry=retry, error_code=error.code)
            return {**self._promotion_view(failed, evidence_ids), "idempotent_reuse": False}
        committed = self._transition(
            item, asset, record, "VERIFIED", action="VERIFY", retry=retry,
            public_ref=str(verified.get("public_ref") or record.get("public_ref") or ""),
        )
        return {**self._promotion_view(committed, evidence_ids), "idempotent_reuse": False}

    def get_item(self, item_id: str) -> dict[str, Any]:
        item, asset, evidence_ids = self._context(item_id)
        record = self._record(item, asset)
        if record is None:
            raise HardwareR1PromotionError("PROMOTION_NOT_FOUND")
        view = self._promotion_view(record, evidence_ids)
        pending = self._pending_operations(str(asset["candidate_id"]))
        if not pending:
            return {
                **view,
                "reconciliation_required": False,
                "reconciliation_operation_type": None,
                "reconciliation_error_code": None,
            }
        operation_type = str(pending[0].get("operation_type") or "")
        return {
            **view,
            "reconciliation_required": True,
            "reconciliation_operation_type": operation_type,
            "reconciliation_error_code": self._reconciliation_error(operation_type),
        }

    def intake_batch(self, batch_id: str) -> dict[str, Any]:
        try:
            batch = self.workbench.get_batch(batch_id)
        except Exception as error:
            raise HardwareR1PromotionError(str(getattr(error, "code", None) or "BATCH_NOT_FOUND")) from error
        results = []
        for item in batch.get("items") or []:
            item_id = str(item.get("item_id") or "")
            if not item_id:
                continue
            if item.get("result") not in {"CANDIDATE_READY", "REVIEW"}:
                results.append({"item_id": item_id, "status": "SKIPPED", "error_code": "GOLDEN_CANDIDATE_NOT_READY"})
                continue
            try:
                results.append(self.intake_item(item_id))
            except HardwareR1PromotionError as error:
                results.append({"item_id": item_id, "status": "FAILED", "error_code": error.code})
        return self._batch_result(batch_id, results)

    def retry_failed_item(self, item_id: str) -> dict[str, Any]:
        current = self.get_item(item_id)
        _, asset, _ = self._context(item_id)
        self._raise_for_pending_operation(str(asset["candidate_id"]))
        if current["status"] == "INTAKE_FAILED":
            return self.intake_item(item_id, retry=True)
        if current["status"] == "PUBLISH_FAILED":
            operation_id = self._operation_id(
                str(asset["candidate_id"]), "PUBLISH", PROMOTION_REVISION
            )
            try:
                journal_entry = self.operation_journal.get(operation_id)
            except HardwareAssetOperationJournalError as error:
                raise HardwareR1PromotionError(error.code) from error
            fingerprint = (
                journal_entry.get("request_fingerprint")
                if isinstance(journal_entry, Mapping)
                else {}
            )
            publisher = str(fingerprint.get("publisher") or "hardware-promotion-retry")
            published_at_text = str(fingerprint.get("published_at") or "")
            try:
                published_at = (
                    datetime.fromisoformat(published_at_text)
                    if published_at_text
                    else datetime.now(timezone.utc)
                )
            except ValueError as error:
                raise HardwareR1PromotionError("OPERATION_JOURNAL_INVALID") from error
            return self.publish_item(item_id, publisher=publisher,
                                     published_at=published_at, retry=True)
        if current["status"] == "VERIFY_FAILED":
            return self.verify_item(item_id, retry=True)
        raise HardwareR1PromotionError("PROMOTION_RETRY_NOT_ALLOWED")

    def retry_failed_batch(self, batch_id: str) -> dict[str, Any]:
        try:
            batch = self.workbench.get_batch(batch_id)
        except Exception as error:
            raise HardwareR1PromotionError(str(getattr(error, "code", None) or "BATCH_NOT_FOUND")) from error
        results = []
        for item in batch.get("items") or []:
            item_id = str(item.get("item_id") or "")
            if not item_id:
                continue
            try:
                if self.get_item(item_id)["status"] in {"INTAKE_FAILED", "PUBLISH_FAILED", "VERIFY_FAILED"}:
                    results.append(self.retry_failed_item(item_id))
            except HardwareR1PromotionError as error:
                results.append({"item_id": item_id, "status": "FAILED", "error_code": error.code})
        return self._batch_result(batch_id, results, retry=True)

    def get_batch(self, batch_id: str) -> dict[str, Any]:
        try:
            batch = self.workbench.get_batch(batch_id)
        except Exception as error:
            raise HardwareR1PromotionError(str(getattr(error, "code", None) or "BATCH_NOT_FOUND")) from error
        records = []
        for item in batch.get("items") or []:
            item_id = str(item.get("item_id") or "")
            try:
                records.append(self.get_item(item_id))
            except HardwareR1PromotionError as error:
                if error.code != "PROMOTION_NOT_FOUND":
                    records.append({"item_id": item_id, "status": "FAILED", "error_code": error.code})
        return self._batch_result(batch_id, records)

    def migrate_legacy_records(self) -> dict[str, Any]:
        migrated = 0
        reused = 0
        for legacy in self.store.list_all():
            item_id = str(legacy.get("item_id") or "")
            item, asset, _ = self._context(item_id)
            try:
                existed = self.assets.get_promotion_record(str(asset["candidate_id"]))
            except CandidateAssetRepositoryError as error:
                raise HardwareR1PromotionError(error.code) from error
            record = self._migrate_legacy(item, asset, legacy, existed)
            if existed is None:
                migrated += 1
            else:
                reused += 1
            if record["promotion_status"] != str(legacy.get("status") or ""):
                raise HardwareR1PromotionError("PROMOTION_IDEMPOTENCY_CONFLICT")
        return {"status": "PASS", "migrated": migrated, "idempotent_reuse": reused}

    @staticmethod
    def _batch_result(
        batch_id: str,
        records: list[dict[str, Any]],
        *,
        retry: bool = False,
    ) -> dict[str, Any]:
        summary: dict[str, int] = {"TOTAL": len(records)}
        for record in records:
            status = str(record.get("status") or "UNKNOWN")
            summary[status] = summary.get(status, 0) + 1
        return {
            "contract_version": PROMOTION_CONTRACT_VERSION,
            "batch_id": batch_id,
            "summary": summary,
            "items": records,
            "retry_failed_only": retry,
            "auto_publish": False,
        }


__all__ = [
    "PROMOTION_CONTRACT_VERSION",
    "PROMOTION_REVISION",
    "HardwareR1KnowledgePromotionService",
    "HardwareR1KnowledgePromotionStore",
    "HardwareR1PromotionError",
]
