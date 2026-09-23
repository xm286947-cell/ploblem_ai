from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from repositories import JsonArtifactRepository

from .candidate import business_source_ref_key
from .models import (
    CandidateSourceType,
    ConflictStatus,
    DuplicateStatus,
    EvidenceValidationStatus,
    KnowledgeCandidate,
    KnowledgeEvaluation,
)


class KnowledgeEvaluationError(RuntimeError):
    """Stable KP-D02 evaluation failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _normalize_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"\s+", " ", text)


def _semantic_identity(payload: dict[str, Any]) -> tuple[Any, ...]:
    object_type = str(payload.get("object_type") or payload.get("type") or "")
    title = (
        payload.get("title")
        or payload.get("title_or_name")
        or payload.get("name")
        or ""
    )
    device = payload.get("device_type") or payload.get("applicable_device_type")
    if isinstance(device, list):
        device_key = tuple(sorted(_normalize_text(item) for item in device))
    else:
        device_key = (_normalize_text(device),) if device else ()
    scope = payload.get("scope") or []
    if not isinstance(scope, list):
        scope = [scope]
    return (
        object_type.upper(),
        _normalize_text(title),
        device_key,
        tuple(sorted(_normalize_text(item) for item in scope if item)),
    )


def _knowledge_content(payload: dict[str, Any]) -> str:
    for key in (
        "content",
        "summary_or_content",
        "definition",
        "mechanism",
        "recommended_action",
    ):
        if payload.get(key):
            return _normalize_text(payload[key])
    return ""


def _object_id(payload: dict[str, Any], fallback: str) -> str:
    return str(
        payload.get("object_id")
        or payload.get("knowledge_id")
        or payload.get("id")
        or fallback
    )


class KnowledgeEvaluationService:
    """Evaluate a candidate before human review without mutating knowledge."""

    EVALUATION_VERSION = "kp-d02-v1"

    def __init__(self, repository: JsonArtifactRepository) -> None:
        self.repository = repository

    def evaluate(self, candidate: KnowledgeCandidate) -> KnowledgeEvaluation:
        evidence_status, evidence_reasons = self._validate_evidence(candidate)
        source_valid, source_reasons = self._validate_source(candidate)
        contract_valid = candidate.contract_version == "knowledge-candidate/v1"
        scope_valid = bool(candidate.scope)
        candidate_complete = bool(
            candidate.candidate_id
            and candidate.object_type
            and candidate.title.strip()
            and candidate.content.strip()
            and candidate.source_refs
        )

        published = self._published_objects()
        duplicate_status, duplicate_ids = self._detect_duplicates(
            candidate, published
        )
        conflict_status, conflict_ids = self._detect_conflicts(
            candidate, published
        )

        reasons: list[str] = []
        reasons.extend(evidence_reasons)
        reasons.extend(source_reasons)
        if not contract_valid:
            reasons.append("KNOWLEDGE_CONTRACT_INVALID")
        if not scope_valid:
            reasons.append("SCOPE_INVALID")
        if not candidate_complete:
            reasons.append("CANDIDATE_INCOMPLETE")
        if duplicate_status == DuplicateStatus.DUPLICATE:
            reasons.append("DUPLICATE")
        elif duplicate_status == DuplicateStatus.POSSIBLE_DUPLICATE:
            reasons.append("POSSIBLE_DUPLICATE")
        if conflict_status == ConflictStatus.CONFLICT:
            reasons.append("CONFLICT")

        review_ready = (
            evidence_status != EvidenceValidationStatus.INVALID
            and source_valid
            and contract_valid
            and scope_valid
            and candidate_complete
        )
        publish_readiness = (
            evidence_status == EvidenceValidationStatus.VALID
            and source_valid
            and contract_valid
            and scope_valid
            and candidate_complete
            and duplicate_status == DuplicateStatus.NEW
            and conflict_status == ConflictStatus.NONE
        )

        compared_ids = sorted(
            {
                _object_id(payload, fallback)
                for fallback, payload in published
            }
        )
        evaluation_id = self._evaluation_id(
            candidate,
            compared_ids,
            duplicate_status,
            conflict_status,
        )
        result = KnowledgeEvaluation(
            evaluation_id=evaluation_id,
            candidate_id=candidate.candidate_id,
            evidence_status=evidence_status,
            source_valid=source_valid,
            contract_valid=contract_valid,
            scope_valid=scope_valid,
            candidate_complete=candidate_complete,
            duplicate_status=duplicate_status,
            duplicate_object_ids=duplicate_ids,
            conflict_status=conflict_status,
            conflict_object_ids=conflict_ids,
            publish_readiness=publish_readiness,
            review_ready=review_ready,
            reasons=list(dict.fromkeys(reasons)),
            evaluated_against_object_ids=compared_ids,
            evaluation_version=self.EVALUATION_VERSION,
        )
        self.repository.save(
            (
                "knowledge/production/evaluations/"
                f"{candidate.candidate_id}/{evaluation_id}.json"
            ),
            result.model_dump(mode="json"),
        )
        return result

    def evaluate_by_id(self, candidate_id: str) -> KnowledgeEvaluation:
        payload = self.repository.load(
            f"knowledge/production/candidates/{candidate_id}.json",
            required=True,
        )
        if payload is None:
            raise KnowledgeEvaluationError("CANDIDATE_NOT_FOUND")
        try:
            candidate = KnowledgeCandidate.model_validate(payload)
        except Exception as exc:
            raise KnowledgeEvaluationError(
                "KNOWLEDGE_CONTRACT_INVALID"
            ) from exc
        return self.evaluate(candidate)

    def _validate_evidence(
        self, candidate: KnowledgeCandidate
    ) -> tuple[EvidenceValidationStatus, list[str]]:
        if not candidate.evidence_refs:
            return EvidenceValidationStatus.INVALID, ["EVIDENCE_MISSING"]

        valid = 0
        invalid = 0
        for evidence_id in candidate.evidence_refs:
            payload = self.repository.load(
                f"knowledge/production/evidence/{evidence_id}.json"
            )
            if not isinstance(payload, dict):
                invalid += 1
                continue
            source = payload.get("source")
            locator = payload.get("locator")
            if (
                not isinstance(source, dict)
                or not str(source.get("source_id") or "").strip()
                or not isinstance(locator, dict)
                or not str(locator.get("type") or "").strip()
                or not isinstance(locator.get("value"), dict)
            ):
                invalid += 1
                continue
            valid += 1

        if valid == len(candidate.evidence_refs):
            return EvidenceValidationStatus.VALID, []
        if valid:
            return EvidenceValidationStatus.PARTIAL, ["EVIDENCE_PARTIAL"]
        return EvidenceValidationStatus.INVALID, ["EVIDENCE_MISSING"]

    def _validate_source(
        self, candidate: KnowledgeCandidate
    ) -> tuple[bool, list[str]]:
        if candidate.candidate_source_type == CandidateSourceType.BUSINESS:
            if (
                candidate.business_source_type is None
                or not candidate.business_source_id
            ):
                return False, ["BUSINESS_PROVENANCE_INVALID"]
            expected = business_source_ref_key(
                candidate.business_source_type.value,
                candidate.business_source_id,
                candidate.business_source_version,
            )
            if expected not in candidate.source_refs:
                return False, ["BUSINESS_PROVENANCE_INVALID"]
            return True, []

        for source_ref in candidate.source_refs:
            if "@" not in source_ref:
                return False, ["SOURCE_TRACEABILITY_INVALID"]
            source_id, source_version = source_ref.rsplit("@", 1)
            manifest = self.repository.load(
                (
                    f"knowledge/source_documents/{source_id}/"
                    f"{source_version}/source_document.json"
                )
            )
            if not isinstance(manifest, dict):
                return False, ["SOURCE_UNAVAILABLE"]
            if manifest.get("retrieval_status") not in {None, "PARSED"}:
                return False, ["SOURCE_UNAVAILABLE"]
            if not str(manifest.get("content_hash") or "").strip():
                return False, ["SOURCE_TRACEABILITY_INVALID"]
        return True, []

    def _published_objects(self) -> list[tuple[str, dict[str, Any]]]:
        objects: list[tuple[str, dict[str, Any]]] = []
        for path in self.repository.list("knowledge/production/published"):
            payload = self.repository.load(path)
            if isinstance(payload, dict):
                objects.append((path.stem, payload))
        return objects

    def _detect_duplicates(
        self,
        candidate: KnowledgeCandidate,
        published: list[tuple[str, dict[str, Any]]],
    ) -> tuple[DuplicateStatus, list[str]]:
        candidate_payload = candidate.model_dump(mode="json")
        identity = _semantic_identity(candidate_payload)
        content = _knowledge_content(candidate_payload)

        exact: list[str] = []
        possible: list[str] = []
        for fallback, payload in published:
            if _semantic_identity(payload) != identity:
                continue
            object_id = _object_id(payload, fallback)
            if _knowledge_content(payload) == content:
                exact.append(object_id)
            else:
                possible.append(object_id)

        if exact:
            return DuplicateStatus.DUPLICATE, sorted(set(exact))
        if possible:
            return DuplicateStatus.POSSIBLE_DUPLICATE, sorted(set(possible))
        return DuplicateStatus.NEW, []

    def _detect_conflicts(
        self,
        candidate: KnowledgeCandidate,
        published: list[tuple[str, dict[str, Any]]],
    ) -> tuple[ConflictStatus, list[str]]:
        candidate_payload = candidate.model_dump(mode="json")
        identity = _semantic_identity(candidate_payload)
        content = _knowledge_content(candidate_payload)

        conflicts: list[str] = []
        for fallback, payload in published:
            if str(payload.get("status") or "ACTIVE").upper() != "ACTIVE":
                continue
            if _semantic_identity(payload) != identity:
                continue
            if _knowledge_content(payload) == content:
                continue
            conflicts.append(_object_id(payload, fallback))

        if conflicts:
            return ConflictStatus.CONFLICT, sorted(set(conflicts))
        return ConflictStatus.NONE, []

    @staticmethod
    def _evaluation_id(
        candidate: KnowledgeCandidate,
        compared_ids: list[str],
        duplicate_status: DuplicateStatus,
        conflict_status: ConflictStatus,
    ) -> str:
        material = {
            "candidate": candidate.model_dump(mode="json"),
            "compared_ids": compared_ids,
            "duplicate_status": duplicate_status.value,
            "conflict_status": conflict_status.value,
            "version": KnowledgeEvaluationService.EVALUATION_VERSION,
        }
        digest = hashlib.sha256(
            json.dumps(
                material,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return "KPE-" + digest[:24]
