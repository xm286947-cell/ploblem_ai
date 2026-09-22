"""Idempotent Major Event publication into the existing Historical Case artifacts.

CASE-PUBLISH-001-B owns persistence only. Candidate construction and business
scope validation remain in MajorCasePublishAdapter (001-A). Publication is
committed as a small filesystem transaction: all JSON payloads are staged,
existing artifacts are snapshotted, and publication metadata is promoted last
as the PUBLISHED commit point.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import shutil
from typing import Any, Mapping
import uuid

from builder.retrieval_document_builder import RetrievalDocumentBuilder
from quality_knowledge.major_cases.repository import MajorKnowledgeRepository
from repositories import JsonArtifactRepository

from services.major_case_publish import MajorCasePublishAdapter, PublishValidationError


PUBLICATION_CONTRACT = "case-publish/v1"
PUBLICATION_STATUS = "PUBLISHED"


class PublishCommitError(RuntimeError):
    """A retry-safe publication failure with a stable error code."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


class MajorCasePublisher:
    """Single publication entry point: publish_event(event_id)."""

    def __init__(
        self,
        major_repository: MajorKnowledgeRepository,
        artifact_repository: JsonArtifactRepository,
        *,
        adapter: MajorCasePublishAdapter | None = None,
    ) -> None:
        self.major_repository = major_repository
        self.artifact_repository = artifact_repository
        self.adapter = adapter or MajorCasePublishAdapter(major_repository)
        self.retrieval_builder = RetrievalDocumentBuilder()

    def publish_event(self, event_id: str) -> dict[str, Any]:
        metadata_path = self._metadata_path(event_id)
        existing = self.artifact_repository.load(metadata_path)
        existing_identity = (
            existing.get("source_identity")
            if isinstance(existing, dict)
            and isinstance(existing.get("source_identity"), dict)
            else existing
        )
        candidate = self.adapter.build_candidate(
            event_id,
            existing_mapping=existing_identity if isinstance(existing_identity, Mapping) else None,
        )

        case_id = self._existing_or_stable_case_id(event_id, existing)
        revision = candidate["knowledge_revision"]
        same_published_revision = bool(
            isinstance(existing, dict)
            and existing.get("publication_status") == PUBLICATION_STATUS
            and _text(existing.get("knowledge_revision")) == revision
        )

        if same_published_revision and self._published_artifacts_valid(case_id, revision):
            return self._result(
                "REUSED",
                candidate,
                case_id,
                repaired=False,
            )

        if isinstance(existing, dict) and existing.get("publication_status") == PUBLICATION_STATUS:
            action = "REUSED" if same_published_revision else "UPDATED"
        else:
            action = "CREATED"

        enriched, raw_evidence, retrieval = self._build_artifacts(candidate, case_id)
        now = datetime.now(timezone.utc).isoformat()
        publication_metadata = {
            "publication_contract": PUBLICATION_CONTRACT,
            "publication_status": PUBLICATION_STATUS,
            "source_identity": candidate["source_identity"],
            "case_id": case_id,
            "major_case_id": candidate["major_case_id"],
            "event_id": candidate["event_id"],
            "business_id": candidate.get("standard_itr"),
            "knowledge_revision": revision,
            "last_action": action,
            "created_at": (
                existing.get("created_at")
                if isinstance(existing, dict) and _text(existing.get("created_at"))
                else now
            ),
            "published_at": (
                existing.get("published_at")
                if isinstance(existing, dict)
                and existing.get("publication_status") == PUBLICATION_STATUS
                and _text(existing.get("published_at"))
                else now
            ),
            "updated_at": now,
            "content_hash": retrieval["content_hash"],
            "validation_warnings": list(candidate.get("validation_warnings") or []),
        }

        payloads = {
            self._enriched_path(case_id): enriched,
            self._evidence_path(case_id): raw_evidence,
            self._retrieval_path(case_id): retrieval,
            metadata_path: publication_metadata,
        }
        try:
            self._commit(payloads, metadata_path=metadata_path)
        except PublishCommitError:
            raise
        except Exception as exc:
            raise PublishCommitError("PUBLICATION_WRITE_FAILED") from exc

        return self._result(
            action,
            candidate,
            case_id,
            repaired=bool(same_published_revision),
        )

    def _build_artifacts(
        self,
        candidate: Mapping[str, Any],
        case_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        enriched = deepcopy(candidate["enriched_case"])
        metadata = enriched.setdefault("metadata", {})
        metadata["case_id"] = case_id
        metadata["knowledge_revision"] = candidate["knowledge_revision"]

        knowledge = enriched.setdefault("knowledge", {})
        knowledge.setdefault("normalized_problem", candidate.get("title") or "")
        knowledge["quality_flags"] = list(candidate.get("validation_warnings") or [])

        raw_evidence = deepcopy(candidate["raw_evidence"])
        raw_evidence["case_id"] = case_id
        raw_evidence["knowledge_revision"] = candidate["knowledge_revision"]

        source_path = self._enriched_path(case_id)
        retrieval = self.retrieval_builder.build(enriched, source_path)
        retrieval["filters"]["knowledge_source"] = "MAJOR_EVENT"
        retrieval["filters"]["major_event_id"] = candidate["event_id"]

        self._validate_artifacts(case_id, enriched, raw_evidence, retrieval)
        return enriched, raw_evidence, retrieval

    @staticmethod
    def _validate_artifacts(
        case_id: str,
        enriched: Mapping[str, Any],
        raw_evidence: Mapping[str, Any],
        retrieval: Mapping[str, Any],
    ) -> None:
        if _text((enriched.get("metadata") or {}).get("case_id")) != case_id:
            raise PublishValidationError("PUBLISHED_CASE_ID_MISMATCH")
        if _text(raw_evidence.get("case_id")) != case_id:
            raise PublishValidationError("PUBLISHED_CASE_ID_MISMATCH")
        if _text(retrieval.get("case_id")) != case_id:
            raise PublishValidationError("PUBLISHED_CASE_ID_MISMATCH")
        if not _text(retrieval.get("text")) or not _text(retrieval.get("content_hash")):
            raise PublishValidationError("RETRIEVAL_DOCUMENT_INVALID")

    def _published_artifacts_valid(self, case_id: str, expected_revision: str) -> bool:
        enriched = self.artifact_repository.load(self._enriched_path(case_id))
        evidence = self.artifact_repository.load(self._evidence_path(case_id))
        retrieval = self.artifact_repository.load(self._retrieval_path(case_id))
        if not all(isinstance(item, dict) for item in (enriched, evidence, retrieval)):
            return False
        if _text((enriched.get("metadata") or {}).get("knowledge_revision")) != expected_revision:
            return False
        if _text(evidence.get("knowledge_revision")) != expected_revision:
            return False
        try:
            self._validate_artifacts(case_id, enriched, evidence, retrieval)
        except PublishValidationError:
            return False
        return True

    def _commit(
        self,
        payloads: Mapping[str, dict[str, Any]],
        *,
        metadata_path: str,
    ) -> None:
        """Stage all payloads, then promote with rollback; metadata is last."""
        stage_id = uuid.uuid4().hex
        stage_root = f"knowledge/.publication_staging/{stage_id}"
        staged: dict[str, Path] = {}
        previous: dict[str, bytes | None] = {}
        promoted: list[str] = []

        ordered = [path for path in payloads if path != metadata_path] + [metadata_path]
        try:
            for index, final_path in enumerate(ordered):
                staged_path = f"{stage_root}/{index}.json"
                staged[final_path] = self.artifact_repository.save(
                    staged_path,
                    payloads[final_path],
                )

            for final_path in ordered:
                target = self.artifact_repository.resolve(final_path)
                target.parent.mkdir(parents=True, exist_ok=True)
                previous[final_path] = target.read_bytes() if target.is_file() else None
                self._replace(staged[final_path], target)
                promoted.append(final_path)
        except Exception as exc:
            rollback_error: Exception | None = None
            for final_path in reversed(promoted):
                target = self.artifact_repository.resolve(final_path)
                try:
                    before = previous.get(final_path)
                    if before is None:
                        target.unlink(missing_ok=True)
                    else:
                        restore = target.with_name(f".{target.name}.{uuid.uuid4().hex}.restore")
                        restore.write_bytes(before)
                        self._replace(restore, target)
                except Exception as restore_exc:
                    rollback_error = restore_exc
            if rollback_error is not None:
                raise PublishCommitError("PUBLICATION_ROLLBACK_FAILED") from rollback_error
            raise PublishCommitError("PUBLICATION_WRITE_FAILED") from exc
        finally:
            stage_dir = self.artifact_repository.resolve(stage_root)
            shutil.rmtree(stage_dir, ignore_errors=True)

    @staticmethod
    def _replace(source: str | Path, target: str | Path) -> None:
        os.replace(source, target)

    @staticmethod
    def _stable_case_id(event_id: str) -> str:
        digest = hashlib.sha256(f"MAJOR_EVENT:{event_id}".encode("utf-8")).hexdigest()
        return f"HCASE-{digest[:24].upper()}"

    def _existing_or_stable_case_id(
        self,
        event_id: str,
        existing: Mapping[str, Any] | None,
    ) -> str:
        if isinstance(existing, Mapping):
            existing_case_id = _text(existing.get("case_id"))
            if existing_case_id:
                return existing_case_id
        return self._stable_case_id(event_id)

    @staticmethod
    def _metadata_path(event_id: str) -> str:
        digest = hashlib.sha256(event_id.encode("utf-8")).hexdigest()
        return f"knowledge/publication_metadata/major_event/{digest}.json"

    @staticmethod
    def _enriched_path(case_id: str) -> str:
        return f"knowledge/enriched_case/{case_id}.json"

    @staticmethod
    def _evidence_path(case_id: str) -> str:
        return f"knowledge/raw_evidence/{case_id}.json"

    @staticmethod
    def _retrieval_path(case_id: str) -> str:
        return f"knowledge/retrieval_docs/{case_id}.json"

    @staticmethod
    def _result(
        status: str,
        candidate: Mapping[str, Any],
        case_id: str,
        *,
        repaired: bool,
    ) -> dict[str, Any]:
        return {
            "status": status,
            "publication_status": PUBLICATION_STATUS,
            "case_id": case_id,
            "major_case_id": candidate["major_case_id"],
            "event_id": candidate["event_id"],
            "business_id": candidate.get("standard_itr"),
            "knowledge_revision": candidate["knowledge_revision"],
            "validation_warnings": list(candidate.get("validation_warnings") or []),
            "repaired": repaired,
        }
