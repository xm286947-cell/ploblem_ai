from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from runtime.contracts import (
    CommittedPartialResult,
    PartialResultCandidate,
)
from runtime.content.errors import InvalidPartialResultError


class PartialResultCommitter:
    """Converts only independently valid complete objects into committed partials."""

    def commit(self, candidate: PartialResultCandidate) -> CommittedPartialResult:
        if not candidate.schema_valid:
            raise InvalidPartialResultError(
                "schema-invalid partial cannot be committed",
                details={
                    "execution_key": candidate.execution_key,
                    "chunk_id": candidate.chunk_id,
                    "reason": "SCHEMA_INVALID",
                },
            )
        if not candidate.complete_object:
            raise InvalidPartialResultError(
                "incomplete/truncated object cannot be committed",
                details={
                    "execution_key": candidate.execution_key,
                    "chunk_id": candidate.chunk_id,
                    "finish_reason": candidate.finish_reason,
                    "reason": "INCOMPLETE_OBJECT",
                },
            )

        payload = json.dumps(
            candidate.data,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(
            (
                candidate.execution_key
                + "|"
                + candidate.chunk_id
                + "|"
                + payload
            ).encode("utf-8")
        ).hexdigest()
        return CommittedPartialResult(
            partial_id=f"partial-{digest}",
            chunk_id=candidate.chunk_id,
            execution_key=candidate.execution_key,
            unit_ids=list(candidate.unit_ids),
            data=candidate.data,
            partition_key=candidate.partition_key,
            evidence_ids=list(candidate.evidence_ids),
            committed_at=datetime.now(timezone.utc),
            metadata={
                **candidate.metadata,
                "finish_reason": candidate.finish_reason,
                "independently_validated": True,
            },
        )
