from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from repositories import JsonArtifactRepository
from runtime import (
    AgentConfigLoader,
    AgentRequest,
    ConfiguredAgentRuntime,
    RuntimeStatus,
    SqliteTaskStore,
)

from .candidate import (
    KnowledgeCandidateError,
    KnowledgeCandidateService,
    source_ref_key,
)
from .models import (
    KnowledgeCandidate,
    KnowledgeExtractionOutput,
    SourceDocument,
    StructuredDocument,
)


class KnowledgeExtractionError(RuntimeError):
    """Stable knowledge-extraction failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ExtractionRerunApproval:
    """Explicit operator-supplied authorization for a new failed-task generation.

    The caller is responsible for authenticating the operator and approval_ref.
    This object never auto-approves an extraction or changes source identity.
    """

    generation: int
    previous_task_id: str
    approved_by: str
    approval_ref: str
    reason: str


def _candidate_id(
    source_id: str,
    source_version: str,
    draft: dict[str, Any],
    evidence_ids: list[str],
) -> str:
    material = {
        "source_id": source_id,
        "source_version": source_version,
        "object_type": draft.get("object_type"),
        "title": draft.get("title"),
        "content": draft.get("content"),
        "evidence_ids": evidence_ids,
    }
    digest = hashlib.sha256(
        json.dumps(
            material,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return "KPC-" + digest[:24]


class KnowledgeExtractionService:
    AGENT_ID = "knowledge.production.extract"
    EXTRACTION_VERSION = "kp-m03-v1"

    def __init__(
        self,
        repository: JsonArtifactRepository,
        runtime: Any,
        *,
        agent_id: str = AGENT_ID,
        extraction_version: str = EXTRACTION_VERSION,
    ) -> None:
        self.repository = repository
        self.runtime = runtime
        self.agent_id = agent_id
        self.extraction_version = extraction_version
        self.candidates = KnowledgeCandidateService(repository)

    @classmethod
    def from_project(
        cls,
        project_root: str | Path,
        *,
        model_config_path: str | Path | None = None,
        runtime_db_path: str | Path | None = None,
        environ: dict[str, str] | None = None,
    ) -> "KnowledgeExtractionService":
        root = Path(project_root).resolve()
        loader = AgentConfigLoader(
            root=root,
            model_profiles=(
                model_config_path
                or root / "config/runtime/model.yaml"
            ),
            schemas={
                "KnowledgeExtractionOutput": KnowledgeExtractionOutput,
            },
            environ=environ,
        )
        store = SqliteTaskStore(
            runtime_db_path
            or root / "data/knowledge_production_runtime.sqlite3"
        )
        runtime = ConfiguredAgentRuntime(store, config_loader=loader)
        runtime.load_agent(
            root
            / "config/runtime/agents/knowledge.production.extract.yaml"
        )
        return cls(JsonArtifactRepository(root), runtime)

    def extract(
        self,
        source_document: SourceDocument,
        structured_document: StructuredDocument,
        *,
        requested_topics: list[str] | None = None,
        rerun_approval: ExtractionRerunApproval | None = None,
    ) -> list[KnowledgeCandidate]:
        self._validate_source_pair(source_document, structured_document)
        if structured_document.parse_status != "PARSED":
            raise KnowledgeExtractionError("PDF_PARSE_FAILED")

        topics = list(
            dict.fromkeys(
                item.strip()
                for item in (requested_topics or [])
                if item and item.strip()
            )
        )
        selected_blocks = list(structured_document.blocks)
        if topics:
            lowered = [item.lower() for item in topics]
            selected_blocks = [
                block
                for block in structured_document.blocks
                if any(
                    topic in (
                        (block.source_text or "")
                        + "\n"
                        + (block.section or "")
                    ).lower()
                    for topic in lowered
                )
            ]
            if not selected_blocks:
                raise KnowledgeExtractionError("EVIDENCE_MISSING")

        focus_hash = hashlib.sha256(
            json.dumps(
                topics,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:12]

        logical_request_id = (
            f"knowledge-extract:{source_document.source_id}:"
            f"{source_document.source_version}:"
            f"{source_document.content_hash[:12]}:{focus_hash}"
        )
        # Construct the exact business input first. A rerun must not change
        # even one structured block while retaining the same Source identity.
        request = AgentRequest(
            request_id=logical_request_id,
            agent_id=self.agent_id,
            input={
                "source_document": {
                    "source_id": source_document.source_id,
                    "source_version": source_document.source_version,
                    "publisher": source_document.publisher,
                    "title": source_document.title,
                    "document_type": source_document.document_type,
                    "language": source_document.language,
                },
                "requested_topics": topics,
                "structured_document": {
                    "source_id": structured_document.source_id,
                    "source_version": structured_document.source_version,
                    "pages": [
                        {
                            "page": block.page,
                            "section": block.section,
                            "source_anchor": block.source_anchor,
                            "text": block.source_text,
                        }
                        for block in selected_blocks
                    ],
                },
            },
            metadata={
                "business_domain": "KNOWLEDGE_PRODUCTION",
                "source_id": source_document.source_id,
                "source_version": source_document.source_version,
            },
        )
        request_id, rerun_metadata = self._resolve_execution_identity(
            logical_request_id, rerun_approval, request.input
        )
        if rerun_metadata:
            request = request.model_copy(
                update={
                    "request_id": request_id,
                    "metadata": {**request.metadata, **rerun_metadata},
                }
            )

        try:
            result = self.runtime.invoke(request)
        except Exception as exc:
            raise KnowledgeExtractionError(
                "KNOWLEDGE_EXTRACTION_FAILED"
            ) from exc

        if getattr(result, "status", None) != RuntimeStatus.COMPLETED:
            raise KnowledgeExtractionError("KNOWLEDGE_EXTRACTION_FAILED")

        try:
            output = KnowledgeExtractionOutput.model_validate(result.data)
        except ValidationError as exc:
            raise KnowledgeExtractionError(
                "KNOWLEDGE_CONTRACT_INVALID"
            ) from exc

        produced: list[KnowledgeCandidate] = []
        for draft in output.candidates:
            evidence_ids: list[str] = []
            source_refs: list[str] = []
            for location in draft.evidence_locations:
                if location.source_id != source_document.source_id:
                    raise KnowledgeExtractionError("SOURCE_UNAVAILABLE")
                if (
                    location.source_version
                    != source_document.source_version
                ):
                    raise KnowledgeExtractionError(
                        "SOURCE_VERSION_UNKNOWN"
                    )
                try:
                    evidence = self.candidates.bind_evidence(location)
                except KnowledgeCandidateError as exc:
                    if exc.code in {
                        "EVIDENCE_MISSING",
                        "SOURCE_UNAVAILABLE",
                    }:
                        raise KnowledgeExtractionError(exc.code) from exc
                    raise KnowledgeExtractionError(
                        "KNOWLEDGE_EXTRACTION_FAILED"
                    ) from exc
                evidence_ids.append(evidence.evidence_id)
                source_refs.append(
                    source_ref_key(
                        location.source_id,
                        location.source_version,
                    )
                )

            if not evidence_ids:
                raise KnowledgeExtractionError("EVIDENCE_MISSING")

            draft_payload = draft.model_dump(mode="json")
            candidate = KnowledgeCandidate(
                candidate_id=_candidate_id(
                    source_document.source_id,
                    source_document.source_version,
                    draft_payload,
                    evidence_ids,
                ),
                candidate_source_type="EXTERNAL_SOURCE",
                object_type=draft.object_type,
                title=draft.title,
                summary=draft.summary,
                content=draft.content,
                device_type=draft.device_type,
                scope=draft.scope,
                conditions=draft.conditions,
                limitations=draft.limitations,
                tags=draft.tags,
                evidence_refs=list(dict.fromkeys(evidence_ids)),
                source_refs=list(dict.fromkeys(source_refs)),
                extraction_version=self.extraction_version,
                confidence=draft.confidence,
                status="CANDIDATE",
                producer="KNOWLEDGE_EXTRACTION",
                contract_version="knowledge-candidate/v1",
                created_at=source_document.created_at,
                metadata={
                    "runtime_task_id": getattr(
                        result, "task_id", None
                    ),
                    "runtime_request_id": request.request_id,
                    "unknowns_or_gaps": output.unknowns_or_gaps,
                },
            )
            try:
                produced.append(
                    self.candidates.save_candidate(candidate)
                )
            except KnowledgeCandidateError as exc:
                if exc.code in {
                    "EVIDENCE_MISSING",
                    "SOURCE_UNAVAILABLE",
                    "SOURCE_TRACEABILITY_INVALID",
                }:
                    raise KnowledgeExtractionError(exc.code) from exc
                raise KnowledgeExtractionError(
                    "KNOWLEDGE_EXTRACTION_FAILED"
                ) from exc
        return produced

    def _resolve_execution_identity(
        self,
        logical_request_id: str,
        approval: ExtractionRerunApproval | None,
        business_input: Any,
    ) -> tuple[str, dict[str, Any]]:
        if approval is None:
            return logical_request_id, {}
        if not isinstance(approval, ExtractionRerunApproval):
            raise KnowledgeExtractionError("EXTRACTION_RERUN_APPROVAL_INVALID")

        generation = approval.generation
        if (
            type(generation) is not int
            or generation < 1
            or not all(
                isinstance(value, str) and value.strip()
                for value in (
                    approval.previous_task_id,
                    approval.approved_by,
                    approval.approval_ref,
                    approval.reason,
                )
            )
        ):
            raise KnowledgeExtractionError("EXTRACTION_RERUN_APPROVAL_INVALID")

        predecessor_request_id = (
            logical_request_id
            if generation == 1
            else f"{logical_request_id}:rerun:{generation - 1}"
        )
        # Use the durable TaskRecord, not TaskSnapshot (which lacks input_hash).
        # This is read-only and does not alter the Runtime state machine.
        store = getattr(self.runtime, "store", None)
        get_task_record = getattr(store, "get_task", None)
        if not callable(get_task_record):
            raise KnowledgeExtractionError("EXTRACTION_RERUN_RUNTIME_UNAVAILABLE")
        predecessor = get_task_record(approval.previous_task_id)
        if predecessor is None:
            raise KnowledgeExtractionError(
                "EXTRACTION_RERUN_PREDECESSOR_NOT_FOUND"
            )
        if (
            predecessor.request_id != predecessor_request_id
            or predecessor.status != RuntimeStatus.FAILED
        ):
            raise KnowledgeExtractionError("EXTRACTION_RERUN_PREDECESSOR_INVALID")

        # Match runtime.engine.runtime._hash_payload(request.input) byte-for-byte.
        # Request identity alone does NOT cover structured page/block content.
        input_hash = hashlib.sha256(
            json.dumps(
                business_input,
                sort_keys=True,
                ensure_ascii=False,
                default=str,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if predecessor.input_hash != input_hash:
            raise KnowledgeExtractionError("EXTRACTION_RERUN_INPUT_MISMATCH")

        execution_request_id = f"{logical_request_id}:rerun:{generation}"
        return execution_request_id, {
            "extraction_rerun": {
                "logical_request_id": logical_request_id,
                "generation": generation,
                "previous_request_id": predecessor_request_id,
                "previous_task_id": predecessor.task_id,
                "previous_status": RuntimeStatus.FAILED.value,
                "input_hash": input_hash,
                "approved_by": approval.approved_by.strip(),
                "approval_ref": approval.approval_ref.strip(),
                "reason": approval.reason.strip(),
            }
        }

    @staticmethod
    def _validate_source_pair(
        source_document: SourceDocument,
        structured_document: StructuredDocument,
    ) -> None:
        if source_document.source_id != structured_document.source_id:
            raise KnowledgeExtractionError("SOURCE_UNAVAILABLE")
        if (
            source_document.source_version
            != structured_document.source_version
        ):
            raise KnowledgeExtractionError("SOURCE_VERSION_UNKNOWN")
