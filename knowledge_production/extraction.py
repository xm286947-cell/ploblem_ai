from __future__ import annotations

import hashlib
import json
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
    ) -> list[KnowledgeCandidate]:
        self._validate_source_pair(source_document, structured_document)
        if structured_document.parse_status != "PARSED":
            raise KnowledgeExtractionError("PDF_PARSE_FAILED")

        request = AgentRequest(
            request_id=(
                f"knowledge-extract:{source_document.source_id}:"
                f"{source_document.source_version}:{source_document.content_hash[:12]}"
            ),
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
                        for block in structured_document.blocks
                    ],
                },
            },
            metadata={
                "business_domain": "KNOWLEDGE_PRODUCTION",
                "source_id": source_document.source_id,
                "source_version": source_document.source_version,
            },
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
