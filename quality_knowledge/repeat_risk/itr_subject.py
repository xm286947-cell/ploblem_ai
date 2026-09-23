from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol
import uuid

from .repository import RepeatQueryTraceRepository


ITR_RESOLUTION_WORKBENCH = "ITR_RESOLUTION_WORKBENCH"


class RepeatITRContractError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class ITRSubjectSource(Protocol):
    """Host-owned ITR source boundary.

    Repeat Risk receives ITR facts through this interface and never owns the
    ITR master record itself.
    """

    def get_itr(self, itr_ref: str) -> dict[str, Any] | None: ...

    def get_related_missed_test_ref(self, itr_ref: str) -> str | None: ...

    def get_missed_test(self, missed_test_ref: str) -> dict[str, Any] | None: ...


@dataclass(frozen=True)
class RepeatQuerySubject:
    itr_ref: str
    itr_snapshot: dict[str, Any]
    source: str = ITR_RESOLUTION_WORKBENCH

    def as_dict(self) -> dict[str, Any]:
        return {
            "itr_ref": self.itr_ref,
            "itr_snapshot": deepcopy(self.itr_snapshot),
            "source": self.source,
        }


def _text(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _first(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping.get(key) not in (None, ""):
            return mapping.get(key)
    return None


class RepeatITRService:
    """Compose one Repeat query subject from the ITR workbench.

    The current ITR is always the required subject.  A related missed-test
    issue is optional context and is included only after explicit user choice.
    """

    def __init__(
        self,
        source: ITRSubjectSource,
        repository: RepeatQueryTraceRepository,
        *,
        algorithm_version: str = "repeat-risk/v1",
    ) -> None:
        self.source = source
        self.repository = repository
        self.algorithm_version = algorithm_version

    def inspect_subject(self, itr_ref: str) -> dict[str, Any]:
        subject = self._subject(itr_ref)
        missed_ref = _text(self.source.get_related_missed_test_ref(itr_ref))
        return {
            "subject": subject.as_dict(),
            "optional_context": {
                "missed_test_available": bool(missed_ref),
                "missed_test_ref": missed_ref,
            },
        }

    def create_query(
        self,
        itr_ref: str,
        *,
        include_missed_test: bool = False,
        missed_test_ref: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        subject = self._subject(itr_ref)
        related_ref = _text(self.source.get_related_missed_test_ref(itr_ref))
        requested_ref = _text(missed_test_ref)

        optional_context: dict[str, Any] | None = None
        effective_missed_ref: str | None = None

        if include_missed_test:
            effective_missed_ref = requested_ref or related_ref
            if not effective_missed_ref:
                raise RepeatITRContractError("MISSED_TEST_NOT_AVAILABLE")
            if related_ref and effective_missed_ref != related_ref:
                raise RepeatITRContractError("MISSED_TEST_NOT_RELATED")
            missed = self.source.get_missed_test(effective_missed_ref)
            if not missed:
                raise RepeatITRContractError("MISSED_TEST_NOT_FOUND")
            optional_context = {
                "missed_test_ref": effective_missed_ref,
                "snapshot": deepcopy(missed),
            }

        query_id = "RQ-" + uuid.uuid4().hex
        trace = {
            "query_id": query_id,
            "subject_ref": subject.itr_ref,
            "itr_version": str(subject.itr_snapshot.get("itr_version") or ""),
            "itr_snapshot": subject.itr_snapshot,
            "include_missed_test": bool(include_missed_test),
            "missed_test_ref": effective_missed_ref,
            "optional_context": optional_context,
            "query_time": datetime.now(timezone.utc).isoformat(),
            "algorithm_version": self.algorithm_version,
            "correlation_id": correlation_id or "CORR-" + uuid.uuid4().hex,
        }
        saved = self.repository.save(trace)
        return {
            "query_id": saved["query_id"],
            "subject": subject.as_dict(),
            "optional_context": deepcopy(saved["optional_context"]),
            "trace": saved,
        }

    def _subject(self, itr_ref: str) -> RepeatQuerySubject:
        ref = _text(itr_ref)
        if not ref:
            raise RepeatITRContractError("ITR_REF_REQUIRED")
        raw = self.source.get_itr(ref)
        if not raw:
            raise RepeatITRContractError("ITR_NOT_FOUND")

        itr_id = _text(_first(raw, "itr_id", "business_issue_id", "id")) or ref
        snapshot = {
            "itr_id": itr_id,
            "problem_description": _text(
                _first(raw, "problem_description", "description", "problem")
            ),
            "product": _text(_first(raw, "product", "product_name")),
            "version": _text(_first(raw, "version", "product_version")),
            "scene": _text(_first(raw, "scene", "scenario", "lifecycle_scene")),
            "existing_context": deepcopy(
                _first(raw, "existing_context", "context", "analysis_context") or {}
            ),
            "itr_version": _text(
                _first(raw, "itr_version", "version_id", "issue_version_id", "version_no")
            ),
            "source": ITR_RESOLUTION_WORKBENCH,
        }
        if not snapshot["problem_description"]:
            raise RepeatITRContractError("ITR_PROBLEM_DESCRIPTION_REQUIRED")
        return RepeatQuerySubject(itr_ref=ref, itr_snapshot=snapshot)
