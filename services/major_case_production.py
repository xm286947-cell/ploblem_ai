"""Production orchestration for the Major Source to Historical Case flow.

This service deliberately composes the existing Major repository, D01 runtime
adapter and publisher.  It does not create a second persistence model or give
callers a shortcut around human review.
"""
from __future__ import annotations

from pathlib import Path
import logging
from tempfile import TemporaryDirectory
from typing import Any

from runtime import EvidenceLocator, EvidenceReference, LightweightExecutionEngine, SourceRef, SqliteTaskStore
from runtime.adapters import MajorIssueD01RuntimeAdapter, MajorIssueObjectSpec
from quality_knowledge.major_cases.document_parser import parse_document
from quality_knowledge.major_cases.identity import MajorCaseIdentityConflict, MajorCaseIdentityResolver
from quality_knowledge.problem_refs import InvalidSourceProblemItrRef, SourceProblemItrRefV1
from quality_knowledge.major_cases.repository import MajorKnowledgeRepository
from repositories import JsonArtifactRepository
from services.major_case_publisher import MajorCasePublisher, PublishCommitError
from services.major_case_publish import PublishValidationError


logger = logging.getLogger(__name__)

ENTRY_TYPES = ("ISSUE_FACT", "ROOT_CAUSE", "ACTION", "VERIFICATION")


class MajorProductionError(RuntimeError):
    """Stable error used by the Web entry point."""

    def __init__(self, code: str, *, task_id: str | None = None):
        self.code = code
        self.task_id = task_id
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
        self.identity_resolver = MajorCaseIdentityResolver(repository)
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

        try:
            resolution = self.identity_resolver.resolve_or_create_case(
                group_code=group_code,
                title=title,
                domain=domain,
                source_key=f"ITR:{standard_itr}",
                standard_itrs=[standard_itr],
            )
            case = resolution["case"]
            event = resolution["events"][0]
        except MajorCaseIdentityConflict as error:
            raise MajorProductionError(error.code) from error
        self.repository.update_case_status(case["case_id"], "ACTIVE")
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

    def analyze(self, case_id: str, event_id: str = "") -> dict[str, Any]:
        if self.provider is None:
            raise MajorProductionError("MAJOR_ANALYSIS_PROVIDER_NOT_CONFIGURED")
        detail = self.repository.case_detail(case_id)
        if not detail:
            raise MajorProductionError("MAJOR_CASE_NOT_FOUND")
        events = self.repository.events(case_id)
        if event_id:
            event = next((item for item in events if item["event_id"] == event_id), None)
            if event is None:
                raise MajorProductionError("MAJOR_EVENT_NOT_FOUND")
        elif len(events) == 1:
            event = events[0]
        else:
            raise MajorProductionError("MAJOR_ANALYSIS_REQUIRES_EVENT_SELECTION")
        links = [link for link in self.repository.source_links(case_id) if link.get("event_id") == event["event_id"]]
        if not links:
            raise MajorProductionError("MAJOR_SOURCE_NOT_FOUND")

        # Prefer a parsed review/source document when present. Excel Source Fact
        # is a first-class fallback and uses the same Runtime + review pipeline.
        document_link = next(
            (link for link in links if link.get("source_type") == "MAJOR_SOURCE_DOCUMENT"),
            None,
        )
        link = document_link or next(
            (link for link in links if link.get("source_type") == "MAJOR_EXCEL_SOURCE_FACT"),
            links[0],
        )
        record_id = str(link.get("record_id") or "")
        version = self.repository.version(record_id)
        if version:
            source = SourceRef(
                source_id=record_id,
                source_type="MAJOR_SOURCE_DOCUMENT",
                revision=str(version.get("version_no") or "1"),
                content_hash=str(version["content_hash"]),
                fingerprint=str(version["content_hash"]),
                uri=f"major-source://{record_id}",
            )
            fragments = self.repository.fragments(record_id)
            # Never silently discard sections 21+ of a parsed review PDF/DOCX.
            source_text = "\n".join(str(item.get("text_content") or "") for item in fragments)
            provider_fragments = [
                {
                    "fragment_id": str(item["fragment_id"]),
                    "section_path": str(item.get("section_path") or ""),
                    "location_ref": str(item.get("location_ref") or ""),
                    "text_content": str(item.get("text_content") or ""),
                    "source": source.model_dump(mode="json"),
                }
                for item in fragments
            ]
            source_revision_id = record_id
        else:
            with self.repository.connect() as connection:
                fact = connection.execute(
                    "SELECT * FROM kb_source_fact_revision WHERE source_fact_revision_id=? AND case_id=?",
                    (record_id, case_id),
                ).fetchone()
            if not fact:
                raise MajorProductionError("MAJOR_SOURCE_VERSION_NOT_FOUND")
            fact = dict(fact)
            source_hash = str(fact.get("source_hash") or "")
            source = SourceRef(
                source_id=record_id,
                source_type="MAJOR_EXCEL_SOURCE_FACT",
                revision=str(fact.get("revision_no") or "1"),
                content_hash=source_hash,
                fingerprint=source_hash,
                uri=f"major-excel://{record_id}",
            )
            normalized = str(fact.get("normalized_json") or "{}")
            raw = str(fact.get("raw_json") or "{}")
            source_text = normalized + "\n" + raw
            provider_fragments = [
                {
                    "fragment_id": record_id,
                    "section_path": "STRUCTURED SOURCE FACT",
                    "location_ref": record_id,
                    "text_content": source_text,
                    "source": source.model_dump(mode="json"),
                }
            ]
            source_revision_id = record_id

        # A linked Excel Source Fact and a PDF review are *two distinct evidence
        # sources*, not alternatives. Keep their SourceRef and locator separate.
        # Only include facts linked to this exact Event; never borrow another
        # Event's assertions from the same multi-event Case.
        excel_fragments = []
        seen_fact_ids: set[str] = set()
        for fact_link in links:
            if fact_link.get("source_type") != "MAJOR_EXCEL_SOURCE_FACT":
                continue
            fact_id = str(fact_link.get("record_id") or "")
            if fact_id in seen_fact_ids or not fact_id:
                continue
            seen_fact_ids.add(fact_id)
            if fact_id == source_revision_id:
                continue  # Excel-only fallback was already built above.
            with self.repository.connect() as connection:
                fact_row = connection.execute(
                    "SELECT source_fact_revision_id,revision_no,source_hash,normalized_json,raw_json "
                    "FROM kb_source_fact_revision WHERE source_fact_revision_id=? AND case_id=?",
                    (fact_id, case_id),
                ).fetchone()
            if fact_row is None:
                raise MajorProductionError("MAJOR_EXCEL_SOURCE_FACT_NOT_FOUND")
            fact_source = SourceRef(
                source_id=fact_id,
                source_type="MAJOR_EXCEL_SOURCE_FACT",
                revision=str(fact_row["revision_no"] or "1"),
                content_hash=str(fact_row["source_hash"]),
                fingerprint=str(fact_row["source_hash"]),
                uri=f"major-excel://{fact_id}",
            )
            fact_text = str(fact_row["normalized_json"] or "{}") + "\n" + str(fact_row["raw_json"] or "{}")
            excel_fragments.append({
                "fragment_id": fact_id,
                "section_path": "STRUCTURED SOURCE FACT",
                "location_ref": fact_id,
                "text_content": fact_text,
                "source": fact_source.model_dump(mode="json"),
            })
        provider_fragments = excel_fragments + provider_fragments
        if not provider_fragments or not any(item["text_content"].strip() for item in provider_fragments):
            raise MajorProductionError("MAJOR_ANALYSIS_SOURCE_TEXT_EMPTY")
        # The real provider bridge consumes 'fragments', not source_text.
        # Keep source_text consistent for injected providers and test harnesses.
        source_text = "\n".join(item["text_content"] for item in provider_fragments)
        logger.info(
            "MAJOR_ANALYSIS_INPUT_READY case_id=%s event_id=%s fragments=%d "
            "excel_facts=%d source_chars=%d",
            case_id, event["event_id"], len(provider_fragments),
            sum(f["source"]["source_type"] == "MAJOR_EXCEL_SOURCE_FACT" for f in provider_fragments),
            len(source_text),
        )
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
            issue_version_id=source_revision_id,
            partition_key=event["event_id"],
            source=source,
            expected_objects=specs,
            provider_input={
                "case_id": case_id,
                "event_id": event["event_id"],
                "standard_itr": event.get("standard_itr"),
                "title": detail.get("title"),
                "source_text": source_text,
                "source": source.model_dump(mode="json"),
                "fragments": provider_fragments,
            },
        )
        if not outcome.business_consumable:
            committed = {str(item.get("object_id") or "") for item in outcome.committed_objects}
            missing = [
                entry_type for entry_type in ENTRY_TYPES
                if f"{event['event_id']}:{entry_type}" not in committed
            ]
            logger.warning(
                "MAJOR_ANALYSIS_INCOMPLETE task_id=%s case_id=%s event_id=%s "
                "runtime_status=%s provider_calls=%d committed=%d missing_types=%s gate_passed=%s",
                outcome.task_id, case_id, event["event_id"], outcome.status,
                outcome.provider_calls, len(committed), ",".join(missing),
                bool(outcome.gate and outcome.gate.passed),
            )
            raise MajorProductionError("MAJOR_ANALYSIS_INCOMPLETE", task_id=outcome.task_id)

        # New analysis can replace only pending AI candidates; confirmed human
        # revisions remain immutable until another explicit review action.
        self.repository.clear_pending_ai_entries_for_event(case_id, event["event_id"])
        created: list[dict[str, Any]] = []
        objects = {item["object_id"]: item for item in outcome.committed_objects}
        for entry_type in ENTRY_TYPES:
            item = objects.get(f"{event['event_id']}:{entry_type}")
            if not item:
                logger.warning(
                    "MAJOR_ANALYSIS_MISSING_OBJECT task_id=%s case_id=%s event_id=%s entry_type=%s",
                    outcome.task_id, case_id, event["event_id"], entry_type,
                )
                raise MajorProductionError("MAJOR_ANALYSIS_INCOMPLETE", task_id=outcome.task_id)
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
