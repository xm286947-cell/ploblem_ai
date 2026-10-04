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
    ) -> None:
        self.store = store
        self.workbench = workbench_service
        self.bridge = bridge
        self.assets = candidate_repository

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
                    str(asset["candidate_id"]),
                    expected_status="NOT_STARTED",
                    new_status="PRECHECK_PASS",
                    expected_row_version=int(asset["row_version"]),
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

    def intake_item(self, item_id: str, *, retry: bool = False) -> dict[str, Any]:
        prechecked = self.precheck_item(item_id)
        item, asset, evidence_ids = self._context(item_id)
        record = self._record(item, asset)
        if record is None:
            raise HardwareR1PromotionError("PROMOTION_NOT_FOUND")
        view = self._promotion_view(record, evidence_ids)
        if record["promotion_status"] in {"CANDIDATE_INTAKED", "REVIEW_CONFIRMED",
                "PUBLISHED_PENDING_QUERY_BACK", "VERIFIED"}:
            return {**view, "idempotent_reuse": True}
        if record["promotion_status"] not in {"PRECHECK_PASS", "INTAKE_FAILED"}:
            raise HardwareR1PromotionError("PROMOTION_STATE_INVALID")
        try:
            result = self.bridge.intake(asset["knowledge_object"], item["evidence_validation"])
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

    def _workbench_item(self, item_id: str) -> dict[str, Any]:
        try:
            item = self.workbench.get_item(item_id)
        except Exception as error:
            code = str(getattr(error, "code", None) or "BATCH_ITEM_NOT_FOUND")
            raise HardwareR1PromotionError(code) from error
        if item.get("result") not in {"CANDIDATE_READY", "REVIEW"}:
            raise HardwareR1PromotionError("GOLDEN_CANDIDATE_NOT_READY")
        candidate = item.get("candidate")
        validation = item.get("evidence_validation")
        if not isinstance(candidate, Mapping):
            raise HardwareR1PromotionError("GOLDEN_CANDIDATE_REQUIRED")
        if not isinstance(validation, Mapping):
            raise HardwareR1PromotionError("EVIDENCE_GATE_RESULT_REQUIRED")
        return item

    def precheck_item(self, item_id: str) -> dict[str, Any]:
        item = self._workbench_item(item_id)
        candidate = item["candidate"]
        validation = item["evidence_validation"]
        try:
            precheck = self.bridge.precheck(candidate, validation)
        except HardwareR1GoldenBridgeError as error:
            raise HardwareR1PromotionError(error.code) from error
        golden_hash = _json_hash(candidate)
        ledger = self.store.ensure(
            item_id=item["item_id"],
            batch_id=item["batch_id"],
            business_case_id=str(item["business_case_id"]),
            source_id=str(item["source_id"]),
            golden_hash=golden_hash,
        )
        return {
            "contract_version": PROMOTION_CONTRACT_VERSION,
            "precheck": precheck,
            "promotion": ledger,
        }

    def intake_item(self, item_id: str, *, retry: bool = False) -> dict[str, Any]:
        prechecked = self.precheck_item(item_id)
        current = prechecked["promotion"]
        if current["status"] in {
            "CANDIDATE_INTAKED",
            "REVIEW_CONFIRMED",
            "PUBLISHED_PENDING_QUERY_BACK",
            "VERIFIED",
        }:
            return {**current, "idempotent_reuse": True}

        item = self._workbench_item(item_id)
        try:
            intake = self.bridge.intake(
                item["candidate"],
                item["evidence_validation"],
            )
        except HardwareR1GoldenBridgeError as error:
            failed = self.store.update(
                item_id,
                status="INTAKE_FAILED",
                last_action="INTAKE",
                error_code=error.code,
                increment_retry=retry,
            )
            return {**failed, "idempotent_reuse": False}

        candidate = intake["candidate"]
        updated = self.store.update(
            item_id,
            status="CANDIDATE_INTAKED",
            last_action="INTAKE",
            error_code=None,
            candidate_id=str(candidate["candidate_id"]),
            evidence_refs=[str(ref) for ref in candidate.get("evidence_refs") or []],
            increment_retry=retry,
        )
        return {
            **updated,
            "idempotent_reuse": False,
            "intake_status": intake["status"],
        }

    def review_item(
        self,
        item_id: str,
        *,
        reviewer: str,
        confirmed_content: Mapping[str, Any],
        review_time: datetime,
        review_comment: str | None = None,
    ) -> dict[str, Any]:
        current = self.store.get(item_id)
        if current is None or current["status"] == "PRECHECK_PASS":
            current = self.intake_item(item_id)
        if current["status"] == "REVIEW_CONFIRMED":
            return {**current, "idempotent_reuse": True}
        if current["status"] != "CANDIDATE_INTAKED":
            raise HardwareR1PromotionError("CANDIDATE_INTAKE_REQUIRED")

        item = self._workbench_item(item_id)
        try:
            response = self.bridge.review(
                candidate_id=str(current["candidate_id"]),
                original_golden=item["candidate"],
                confirmed_content=confirmed_content,
                reviewer=reviewer,
                review_time=review_time,
                review_comment=review_comment,
            )
        except HardwareR1GoldenBridgeError as error:
            failed = self.store.update(
                item_id,
                status="REVIEW_FAILED",
                last_action="REVIEW",
                error_code=error.code,
            )
            return {**failed, "idempotent_reuse": False}

        updated = self.store.update(
            item_id,
            status="REVIEW_CONFIRMED",
            last_action="REVIEW",
            error_code=None,
            review_status=str(response.get("review_status") or "CONFIRMED"),
        )
        return {**updated, "idempotent_reuse": False}

    def publish_item(
        self,
        item_id: str,
        *,
        publisher: str,
        published_at: datetime,
        retry: bool = False,
    ) -> dict[str, Any]:
        current = self.store.get(item_id)
        if current is None:
            raise HardwareR1PromotionError("PROMOTION_NOT_FOUND")
        if current["status"] == "VERIFIED":
            return {**current, "idempotent_reuse": True}
        if current["status"] == "PUBLISHED_PENDING_QUERY_BACK":
            return {**current, "idempotent_reuse": True}
        if current["status"] not in {"REVIEW_CONFIRMED", "PUBLISH_FAILED"}:
            raise HardwareR1PromotionError("HUMAN_REVIEW_REQUIRED")
        if not current["candidate_id"] or not current["evidence_refs"]:
            raise HardwareR1PromotionError("PROMOTION_STATE_INVALID")

        try:
            published = self.bridge.publish(
                business_case_id=str(current["business_case_id"]),
                candidate_id=str(current["candidate_id"]),
                evidence_refs=list(current["evidence_refs"]),
                publisher=publisher,
                published_at=published_at,
            )
        except HardwareR1GoldenBridgeError as error:
            failed = self.store.update(
                item_id,
                status="PUBLISH_FAILED",
                last_action="PUBLISH",
                error_code=error.code,
                increment_retry=retry,
            )
            return {**failed, "idempotent_reuse": False}

        obj = published.get("object")
        if not isinstance(obj, Mapping) or not obj.get("knowledge_id"):
            failed = self.store.update(
                item_id,
                status="PUBLISH_FAILED",
                last_action="PUBLISH",
                error_code="KNOWLEDGE_OBJECT_ID_MISSING",
                increment_retry=retry,
            )
            return {**failed, "idempotent_reuse": False}

        updated = self.store.update(
            item_id,
            status="PUBLISHED_PENDING_QUERY_BACK",
            last_action="PUBLISH",
            error_code=None,
            knowledge_id=str(obj["knowledge_id"]),
            public_ref=str(current["candidate_id"]),
            increment_retry=retry,
        )
        return {**updated, "idempotent_reuse": False}

    def verify_item(self, item_id: str, *, retry: bool = False) -> dict[str, Any]:
        current = self.store.get(item_id)
        if current is None:
            raise HardwareR1PromotionError("PROMOTION_NOT_FOUND")
        if current["status"] == "VERIFIED":
            return {**current, "idempotent_reuse": True}
        if current["status"] not in {
            "PUBLISHED_PENDING_QUERY_BACK",
            "VERIFY_FAILED",
        }:
            raise HardwareR1PromotionError("PUBLISH_REQUIRED_BEFORE_QUERY_BACK")

        try:
            verified = self.bridge.query_back(str(current["business_case_id"]))
        except HardwareR1GoldenBridgeError as error:
            failed = self.store.update(
                item_id,
                status="VERIFY_FAILED",
                last_action="VERIFY",
                error_code=error.code,
                increment_retry=retry,
            )
            return {**failed, "idempotent_reuse": False}

        obj = verified.get("object")
        if not isinstance(obj, Mapping):
            raise HardwareR1PromotionError("KNOWLEDGE_RESPONSE_INVALID")
        if str(obj.get("knowledge_id") or "") != str(current["knowledge_id"]):
            failed = self.store.update(
                item_id,
                status="VERIFY_FAILED",
                last_action="VERIFY",
                error_code="QUERY_BACK_OBJECT_MISMATCH",
                increment_retry=retry,
            )
            return {**failed, "idempotent_reuse": False}
        if list(obj.get("evidence_refs") or []) != list(current["evidence_refs"]):
            failed = self.store.update(
                item_id,
                status="VERIFY_FAILED",
                last_action="VERIFY",
                error_code="QUERY_BACK_EVIDENCE_MISMATCH",
                increment_retry=retry,
            )
            return {**failed, "idempotent_reuse": False}

        updated = self.store.update(
            item_id,
            status="VERIFIED",
            last_action="VERIFY",
            error_code=None,
            public_ref=str(verified.get("public_ref") or current["public_ref"] or ""),
            increment_retry=retry,
        )
        return {**updated, "idempotent_reuse": False}

    def get_item(self, item_id: str) -> dict[str, Any]:
        value = self.store.get(item_id)
        if value is None:
            raise HardwareR1PromotionError("PROMOTION_NOT_FOUND")
        return value

    def intake_batch(self, batch_id: str) -> dict[str, Any]:
        try:
            batch = self.workbench.get_batch(batch_id)
        except Exception as error:
            code = str(getattr(error, "code", None) or "BATCH_NOT_FOUND")
            raise HardwareR1PromotionError(code) from error
        results = []
        for source_item in batch.get("items") or []:
            item_id = str(source_item.get("item_id") or "")
            if not item_id:
                continue
            if source_item.get("result") not in {"CANDIDATE_READY", "REVIEW"}:
                results.append(
                    {
                        "item_id": item_id,
                        "status": "SKIPPED",
                        "error_code": "GOLDEN_CANDIDATE_NOT_READY",
                    }
                )
                continue
            results.append(self.intake_item(item_id))
        return self._batch_result(batch_id, results)

    def retry_failed_item(self, item_id: str) -> dict[str, Any]:
        current = self.get_item(item_id)
        if current["status"] == "INTAKE_FAILED":
            return self.intake_item(item_id, retry=True)
        if current["status"] == "PUBLISH_FAILED":
            return self.publish_item(
                item_id,
                publisher="hardware-promotion-retry",
                published_at=datetime.now(timezone.utc),
                retry=True,
            )
        if current["status"] == "VERIFY_FAILED":
            return self.verify_item(item_id, retry=True)
        raise HardwareR1PromotionError("PROMOTION_RETRY_NOT_ALLOWED")

    def retry_failed_batch(self, batch_id: str) -> dict[str, Any]:
        records = self.store.list_batch(batch_id)
        results = []
        for record in records:
            if record["status"] not in {"INTAKE_FAILED", "PUBLISH_FAILED", "VERIFY_FAILED"}:
                continue
            results.append(self.retry_failed_item(record["item_id"]))
        return self._batch_result(batch_id, results, retry=True)

    def get_batch(self, batch_id: str) -> dict[str, Any]:
        records = self.store.list_batch(batch_id)
        return self._batch_result(batch_id, records)

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

    def intake_item(self, item_id: str, *, retry: bool = False) -> dict[str, Any]:
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
            result = self.bridge.intake(asset["knowledge_object"], item["evidence_validation"])
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

    # C1 Durable Asset-backed implementations intentionally shadow the legacy
    # orchestration methods above; all local state reads/writes go through the
    # Candidate Asset repository from this point forward.
    def review_item(
        self, item_id: str, *, reviewer: str, confirmed_content: Mapping[str, Any],
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
                confirmed_content=confirmed_content,
                reviewer=reviewer,
                review_time=review_time,
                review_comment=review_comment,
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
            )
            obj = result.get("object")
            if not isinstance(obj, Mapping) or not obj.get("knowledge_id"):
                raise HardwareR1PromotionError("KNOWLEDGE_OBJECT_ID_MISSING")
        except HardwareR1GoldenBridgeError as error:
            failed = self._transition(item, asset, record, "PUBLISH_FAILED", action="PUBLISH",
                                      retry=retry, error_code=error.code)
            return {**self._promotion_view(failed, evidence_ids), "idempotent_reuse": False}
        committed = self._transition(
            item, asset, record, "PUBLISHED_PENDING_QUERY_BACK", action="PUBLISH",
            retry=retry, knowledge_id=str(obj["knowledge_id"]),
            public_ref=str(result.get("public_ref") or record["knowledge_candidate_id"]),
        )
        return {**self._promotion_view(committed, evidence_ids), "idempotent_reuse": False}

    def verify_item(self, item_id: str, *, retry: bool = False) -> dict[str, Any]:
        item, asset, evidence_ids = self._context(item_id)
        record = self._record(item, asset)
        if record is None:
            raise HardwareR1PromotionError("PROMOTION_NOT_FOUND")
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
        return self._promotion_view(record, evidence_ids)

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
        if current["status"] == "INTAKE_FAILED":
            return self.intake_item(item_id, retry=True)
        if current["status"] == "PUBLISH_FAILED":
            return self.publish_item(item_id, publisher="hardware-promotion-retry",
                                     published_at=datetime.now(timezone.utc), retry=True)
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
