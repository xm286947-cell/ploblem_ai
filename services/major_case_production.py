"""Production orchestration for the Major Source to Historical Case flow.

This service deliberately composes the existing Major repository, D01 runtime
adapter and publisher.  It does not create a second persistence model or give
callers a shortcut around human review.
"""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from runtime import EvidenceLocator, EvidenceReference, LightweightExecutionEngine, SourceRef, SqliteTaskStore
from runtime.adapters import MajorIssueD01RuntimeAdapter, MajorIssueObjectSpec
from quality_knowledge.major_cases.document_parser import parse_document
from quality_knowledge.problem_refs import InvalidSourceProblemItrRef, SourceProblemItrRefV1
from quality_knowledge.major_cases.repository import MajorKnowledgeRepository
from repositories import JsonArtifactRepository
from services.major_case_publisher import MajorCasePublisher, PublishCommitError
from services.major_case_publish import PublishValidationError


ENTRY_TYPES = ("ISSUE_FACT", "ROOT_CAUSE", "ACTION", "VERIFICATION")


class MajorProductionError(RuntimeError):
    """Stable error used by the Web entry point."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class MajorCaseProductionService:
    """The only Web-facing path from a new source to a formal publication."""

    def __init__(
        self,
        repository: MajorKnowledgeRepository,
        artifact_repository: JsonArtifactRepository,
        runtime_db_path: str | Path,
        provider: Any | None = None,
    ) -> None:
        self.repository = repository
        self.artifact_repository = artifact_repository
        self.publisher = MajorCasePublisher(repository, artifact_repository)
        self.provider = provider
        self.store = SqliteTaskStore(runtime_db_path)
        self.runtime = LightweightExecutionEngine(self.store)

    def intake_source(
        self,
        *,
        title: str,
        group_code: str,
        domain: str,
        standard_itr: str,
        source_name: str,
        source_bytes: bytes,
    ) -> dict[str, Any]:
        if not all(value.strip() for value in (title, group_code, standard_itr, source_name)):
            raise MajorProductionError("MAJOR_SOURCE_IDENTITY_REQUIRED")
        try:
            public_ref = SourceProblemItrRefV1.from_input(standard_itr)
        except InvalidSourceProblemItrRef as error:
            raise MajorProductionError("INVALID_REF") from error
        standard_itr = public_ref.public_ref
        if not source_bytes:
            raise MajorProductionError("MAJOR_SOURCE_EMPTY")
        suffix = Path(source_name).suffix.lower()
        if suffix not in {".pdf", ".docx", ".doc"}:
            raise MajorProductionError("MAJOR_SOURCE_TYPE_UNSUPPORTED")

        case = self.repository.create_case(title, group_code, domain=domain)
        self.repository.update_case_status(case["case_id"], "ACTIVE")
        event = self.repository.upsert_event(
            case["case_id"],
            standard_itr=standard_itr,
            internal_event_key=standard_itr,
            title=title,
        )
        with TemporaryDirectory(prefix="major-source-") as directory:
            source_path = Path(directory) / Path(source_name).name
            source_path.write_bytes(source_bytes)
            document = self.repository.ingest_file(case["case_id"], source_path)
            parsed = parse_document(self.repository.attachment_path(document["version_id"]))
            fragments = self.repository.save_parse_result(document["version_id"], parsed)

        source_link = self.repository.add_source_link(
            case["case_id"],
            event["event_id"],
            {
                "record_id": document["version_id"],
                "source_type": "MAJOR_SOURCE_DOCUMENT",
                "source_system": "MAJOR_SOURCE_INTAKE",
                "group_code": group_code,
                "source_hash": document["content_hash"],
                "file_name": source_name,
                "version_id": document["version_id"],
                "document_id": document["document_id"],
                "fragment_count": len(fragments),
            },
            standard_itr=standard_itr,
            role="CURRENT_EVENT",
            status="LINKED",
        )
        return {
            "case": case,
            "event": event,
            "document": document,
            "source_link": source_link,
            "parse": {"fragment_count": len(fragments), "warnings": parsed.warnings},
        }

    def analyze(self, case_id: str) -> dict[str, Any]:
        if self.provider is None:
            raise MajorProductionError("MAJOR_ANALYSIS_PROVIDER_NOT_CONFIGURED")
        detail = self.repository.case_detail(case_id)
        if not detail:
            raise MajorProductionError("MAJOR_CASE_NOT_FOUND")
        events = self.repository.events(case_id)
        if len(events) != 1:
            raise MajorProductionError("MAJOR_ANALYSIS_REQUIRES_ONE_EVENT")
        event = events[0]
        links = [link for link in self.repository.source_links(case_id) if link.get("event_id") == event["event_id"]]
        if not links:
            raise MajorProductionError("MAJOR_SOURCE_NOT_FOUND")
        link = links[0]
        version_id = str(link.get("record_id") or "")
        version = self.repository.version(version_id)
        if not version:
            raise MajorProductionError("MAJOR_SOURCE_VERSION_NOT_FOUND")
        source = SourceRef(
            source_id=version_id,
            source_type="MAJOR_SOURCE_DOCUMENT",
            revision=str(version.get("version_no") or "1"),
            content_hash=str(version["content_hash"]),
            fingerprint=str(version["content_hash"]),
            uri=f"major-source://{version_id}",
        )
        fragments = self.repository.fragments(version_id)
        source_text = "\n".join(str(item.get("text_content") or "") for item in fragments[:20])
        specs = [
            MajorIssueObjectSpec(
                object_id=f"{event['event_id']}:{entry_type}",
                unit_id=entry_type,
                locator={"entry_type": entry_type},
                metadata={"entry_type": entry_type},
            )
            for entry_type in ENTRY_TYPES
        ]
        adapter = MajorIssueD01RuntimeAdapter(self.runtime, self.store, self.provider)
        outcome = adapter.execute_partition(
            case_id=case_id,
            issue_version_id=version_id,
            partition_key=event["event_id"],
            source=source,
            expected_objects=specs,
            provider_input={
                "case_id": case_id,
                "event_id": event["event_id"],
                "standard_itr": event.get("standard_itr"),
                "title": detail.get("title"),
                "source_text": source_text,
            },
        )
        if not outcome.business_consumable:
            raise MajorProductionError("MAJOR_ANALYSIS_INCOMPLETE")

        # New analysis can replace only pending AI candidates; confirmed human
        # revisions remain immutable until another explicit review action.
        self.repository.clear_pending_ai_entries_for_event(case_id, event["event_id"])
        created: list[dict[str, Any]] = []
        objects = {item["object_id"]: item for item in outcome.committed_objects}
        for entry_type in ENTRY_TYPES:
            item = objects.get(f"{event['event_id']}:{entry_type}")
            if not item:
                raise MajorProductionError("MAJOR_ANALYSIS_INCOMPLETE")
            data = item.get("data")
            content = data.get("content") if isinstance(data, dict) else data
            content = str(content or "").strip()
            if not content:
                raise MajorProductionError("MAJOR_ANALYSIS_EMPTY_CANDIDATE")
            created.append(self.repository.add_entry(
                case_id,
                entry_type,
                content,
                assertion_kind="AI_INFERENCE",
                origin="AI",
                status="PENDING",
                event_id=event["event_id"],
                evidence=[{
                    "source_link_id": link["source_link_id"],
                    "locator": f"analysis:{entry_type}",
                    "excerpt": content,
                }],
                confidence=0.0,
                explanation="D01_PROVIDER_CANDIDATE",
                analysis_metadata={"runtime_task_id": outcome.task_id, "provider_calls": outcome.provider_calls},
            ))
        return {"case_id": case_id, "event_id": event["event_id"], "runtime_task_id": outcome.task_id, "candidates": created}

    def confirm_entry(self, entry_id: str, *, reviewer: str, content: str = "", reason: str = "") -> dict[str, Any]:
        entry = self.repository.entry(entry_id)
        if not entry:
            raise MajorProductionError("MAJOR_ENTRY_NOT_FOUND")
        if entry.get("status") != "PENDING" or entry.get("origin") != "AI":
            raise MajorProductionError("MAJOR_CONFIRMATION_REQUIRES_PENDING_AI_CANDIDATE")
        if not reviewer.strip():
            raise MajorProductionError("MAJOR_REVIEWER_REQUIRED")
        return self.repository.revise_entry(
            entry_id,
            content.strip() or str(entry.get("content") or ""),
            "CONFIRMED",
            reviewer=reviewer.strip(),
            reason=reason.strip() or "HUMAN_CONFIRMED",
        )

    def publish(self, event_id: str) -> dict[str, Any]:
        try:
            return self.publisher.publish_event(event_id)
        except (PublishValidationError, PublishCommitError) as error:
            raise MajorProductionError(error.code) from error

    def detail(self, case_id: str) -> dict[str, Any]:
        detail = self.repository.case_detail(case_id)
        if not detail:
            raise MajorProductionError("MAJOR_CASE_NOT_FOUND")
        return detail
