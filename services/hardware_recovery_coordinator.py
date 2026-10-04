"""Startup-time deterministic recovery for durable Hardware operations.

This coordinator only replays local Source operations. Remote Knowledge
operations remain in the durable journal for explicit reconciliation and are
reported as asset-scoped pending work; they are never blindly retried here.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from services.hardware_asset_operation_journal import (
    HardwareAssetOperationJournal,
    HardwareAssetOperationJournalError,
)
from services.hardware_case_source_store import (
    HardwareCaseSourceError,
    HardwareCaseSourceStore,
)


LOCAL_SOURCE_OPERATIONS = frozenset({"SOURCE_UPLOAD", "SOURCE_DELETE"})
REMOTE_KNOWLEDGE_OPERATIONS = frozenset(
    {"EVIDENCE_INTAKE", "CANDIDATE_INTAKE", "FORMAL_REVIEW", "PUBLISH"}
)


class HardwareRecoveryError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


class HardwareRecoveryCoordinator:
    """Reconcile deterministic local journal entries before READY is allowed."""

    def __init__(
        self,
        *,
        hardware_db: str | Path,
        asset_db: str | Path,
        source_root: str | Path,
    ) -> None:
        self.hardware_db = Path(hardware_db)
        self.asset_db = Path(asset_db)
        self.source_root = Path(source_root)

    @staticmethod
    def _asset_key(operation: dict[str, Any]) -> str:
        return str(
            operation.get("candidate_id")
            or operation.get("source_id")
            or operation.get("business_case_id")
            or operation.get("operation_id")
        )

    def recover(self) -> dict[str, Any]:
        """Replay Source operations and report any isolated remote ambiguity."""
        journal = HardwareAssetOperationJournal(self.asset_db)
        try:
            pending_before = journal.list_nonterminal()
            unknown = [
                item
                for item in pending_before
                if item["operation_type"] not in LOCAL_SOURCE_OPERATIONS
                and item["operation_type"] not in REMOTE_KNOWLEDGE_OPERATIONS
            ]
            if unknown:
                raise HardwareRecoveryError("RECOVERY_OPERATION_UNKNOWN")

            for item in pending_before:
                if (
                    item["operation_type"] in REMOTE_KNOWLEDGE_OPERATIONS
                    and item["operation_state"] == "REMOTE_SENT"
                ):
                    journal.transition(
                        str(item["operation_id"]),
                        "OUTCOME_UNKNOWN",
                        error_code="REMOTE_OUTCOME_UNKNOWN",
                        recovery_action=(
                            item.get("recovery_action")
                            or "REMOTE_RESULT_NOT_COMMITTED_LOCALLY"
                        ),
                    )

            local = [
                item for item in pending_before
                if item["operation_type"] in LOCAL_SOURCE_OPERATIONS
            ]
            recovered: list[dict[str, Any]] = []
            if local:
                source_store = HardwareCaseSourceStore(
                    self.hardware_db,
                    self.source_root,
                    initialize_schema=False,
                    operation_journal=journal,
                )
                recovered = source_store.recover_source_operations()

            pending_after = journal.list_nonterminal()
        except HardwareRecoveryError:
            raise
        except HardwareAssetOperationJournalError as error:
            raise HardwareRecoveryError(error.code) from error
        except HardwareCaseSourceError as error:
            raise HardwareRecoveryError(error.code) from error
        except (OSError, ValueError) as error:
            raise HardwareRecoveryError("HARDWARE_STARTUP_RECOVERY_REQUIRED") from error

        pending_local = [
            item for item in pending_after
            if item["operation_type"] in LOCAL_SOURCE_OPERATIONS
        ]
        pending_remote = [
            item for item in pending_after
            if item["operation_type"] in REMOTE_KNOWLEDGE_OPERATIONS
        ]
        blocked_local = [
            item for item in recovered
            if item.get("operation_state") == "ASSET_SCOPED_BLOCKED"
        ]
        pending_by_id = {str(item["operation_id"]): item for item in pending_after}
        blocked_keys = {self._asset_key(item) for item in pending_remote}
        blocked_keys.update(
            self._asset_key(pending_by_id[str(item["operation_id"])])
            for item in blocked_local
            if str(item.get("operation_id") or "") in pending_by_id
        )
        last_error = next(
            (str(item.get("error_code")) for item in blocked_local if item.get("error_code")),
            next(
                (
                    str(item.get("error_code"))
                    for item in pending_remote
                    if item.get("error_code")
                ),
                None,
            ),
        )
        if pending_remote and not last_error:
            last_error = "REMOTE_RECONCILIATION_PENDING"
        degraded = bool(pending_remote or blocked_local)
        return {
            "recovery_status": "DEGRADED" if degraded else "COMPLETED",
            "recovery_class": "CLASS_A" if blocked_local else "CLASS_B" if pending_remote else None,
            "active_root_operation": None,
            "pending_local_recovery_count": len(pending_local),
            "pending_remote_reconciliation_count": len(pending_remote),
            "blocked_asset_count": len(blocked_keys),
            "last_recovery_error": last_error,
        }


__all__ = ["HardwareRecoveryCoordinator", "HardwareRecoveryError"]
