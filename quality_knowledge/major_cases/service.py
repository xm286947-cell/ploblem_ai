"""Use cases for REQ-022 intake, association, extraction and review."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable
import tempfile
import json
import re

from quality_knowledge.materials import normalize_itr
from .document_parser import find_itr_mentions, parse_document
from .repository import MajorKnowledgeRepository
from .sources import BusinessSourceGateway, NullBusinessSourceGateway
from .skills import MajorReviewSkillRunner


class MajorCaseService:
    def __init__(
        self,
        repository: MajorKnowledgeRepository,
        source_gateway: BusinessSourceGateway | None = None,
        *,
        skill_model_client=None,
        project_root: str | Path | None = None,
    ):
        self.repository = repository
        self.source_gateway = source_gateway or NullBusinessSourceGateway()
        self.skill_runner = MajorReviewSkillRunner(
            repository,
            project_root=project_root,
            model_client=skill_model_client,
        )
        self.skill_runner.ensure_default_skill()

    def create_case(self, title: str, group_code: str, domain: str = "") -> dict:
        return self.repository.create_case(title, group_code, domain)

    def ingest(
        self,
        case_id: str,
        source_path: str | Path,
        *,
        current_itrs: Iterable[str] = (),
        document_id: str | None = None,
        role: str = "PRIMARY",
    ) -> dict:
        result = self.repository.ingest_file(case_id, source_path, role=role, document_id=document_id)
        version_id = result["version_id"]
        version = self.repository.version(version_id)
        if version["media_type"] == "DOC":
            with self.repository.connect() as connection:
                connection.execute(
                    "UPDATE kb_document_version SET parse_status='FAILED',parse_warnings_json='[\"LEGACY_DOC_UNSUPPORTED_SAVE_AS_DOCX\"]' WHERE version_id=?",
                    (version_id,),
                )
            return {**result, "parse_status": "FAILED", "warnings": ["LEGACY_DOC_UNSUPPORTED_SAVE_AS_DOCX"]}
        existing_fragments = self.repository.fragments(version_id)
        if result["action"] == "SKIPPED" and existing_fragments and version["parse_status"] in {"SUCCESS", "WARNING"}:
            warnings = json.loads(version["parse_warnings_json"] or "[]")
            fragment_count = len(existing_fragments)
            mentions = []
            for fragment in existing_fragments:
                for match in re.findall(r"(?i)\bITR[0-9A-Z_-]{4,}\b", fragment["text_content"]):
                    canonical = normalize_itr(match)
                    if canonical and canonical not in mentions:
                        mentions.append(canonical)
        else:
            parsed = parse_document(self.repository.attachment_path(version_id))
            self.repository.save_parse_result(version_id, parsed)
            warnings = parsed.warnings
            fragment_count = len(parsed.fragments)
            mentions = find_itr_mentions(parsed.fragments)
        normalized_current = []
        for value in current_itrs:
            canonical = normalize_itr(value)
            if canonical and canonical not in normalized_current:
                normalized_current.append(canonical)
        association = self.associate(case_id, normalized_current, [value for value in mentions if value not in normalized_current])
        self.repository.add_tag("CASE", "MAJOR_REVIEW", "重大复盘", "CASE", case_id, derived_from=version_id)
        self.repository.add_tag("DOCUMENT", f"HAS_{version['media_type']}", f"有{version['media_type']}", "CASE", case_id, derived_from=version_id)
        if warnings:
            self.repository.add_tag("QUALITY", "PARSE_WARNING", "解析待处理", "CASE", case_id, derived_from=version_id)
        if association["pending"] or association["conflicts"]:
            self.repository.add_tag("ASSOCIATION", "PENDING_MATCH", "待匹配", "CASE", case_id, derived_from=version_id)
        return {**result, "parse_status": "WARNING" if warnings else "SUCCESS", "warnings": warnings, "fragment_count": fragment_count, "association": association}

    def ingest_upload(self, case_id: str, filename: str, content: bytes, **kwargs) -> dict:
        suffix = Path(filename).suffix
        with tempfile.TemporaryDirectory(prefix="req022-upload-") as directory:
            safe = "".join(char for char in Path(filename).name if char.isalnum() or char in "._- ").strip() or f"upload{suffix}"
            temp = Path(directory) / safe
            temp.write_bytes(content)
            return self.ingest(case_id, temp, **kwargs)

    def associate(self, case_id: str, current_itrs: Iterable[str], historical_itrs: Iterable[str] = ()) -> dict:
        case = self.repository.get_case(case_id)
        if not case:
            raise KeyError(case_id)
        current = [normalize_itr(value) for value in current_itrs if normalize_itr(value)]
        summaries = self.source_gateway.fetch_summaries(case["group_code"], current)
        stats = {"linked": 0, "pending": 0, "conflicts": 0, "events": 0, "historical_references": 0}
        if not current:
            self.repository.upsert_event(case_id, internal_event_key=f"{case_id}:UNLINKED", title="未关联ITR事件")
            stats["events"] = 1
            stats["pending"] += 1
        for itr in current:
            event = self.repository.upsert_event(case_id, standard_itr=itr, internal_event_key=itr, title=itr)
            stats["events"] += 1
            matches = summaries.get(itr, [])
            issue_matches = [item for item in matches if item.get("source_type") in {"QUALITY_ISSUE", "ITR"}]
            conflict = len(issue_matches) > 1
            if not matches:
                self.repository.add_source_link(case_id, event["event_id"], {}, standard_itr=itr, role="CURRENT_EVENT", status="NOT_FOUND")
                stats["pending"] += 1
            else:
                for source in matches:
                    self.repository.add_source_link(case_id, event["event_id"], source, standard_itr=itr, role="CURRENT_EVENT", status="CONFLICT" if conflict else "LINKED")
                if conflict:
                    stats["conflicts"] += 1
                else:
                    stats["linked"] += 1
        for itr in historical_itrs:
            canonical = normalize_itr(itr)
            if not canonical:
                continue
            self.repository.add_source_link(case_id, None, {}, standard_itr=canonical, role="HISTORICAL_REFERENCE", status="PENDING")
            stats["historical_references"] += 1
        return stats

    def reconcile(self, case_id: str) -> dict:
        case = self.repository.get_case(case_id)
        links = self.repository.source_links(case_id)
        itrs = [row["standard_itr"] for row in links if row["relation_role"] == "CURRENT_EVENT"]
        live = self.source_gateway.fetch_summaries(case["group_code"], itrs)
        return self.repository.reconcile_sources(case_id, live)

    def extract(
        self,
        case_id: str,
        version_id: str,
        *,
        skill_version_id: str | None = None,
        retry_failed: bool = False,
        execution_mode: str = "mock",
    ) -> dict:
        return self.skill_runner.run(
            case_id,
            version_id,
            skill_version_id=skill_version_id,
            retry_failed=retry_failed,
            execution_mode=execution_mode,
        )

    def review_entry(self, entry_id: str, *, content: str, action: str, reviewer: str, reason: str = "") -> dict:
        mapping = {"CONFIRM": "CONFIRMED", "CORRECT": "CORRECTED", "REJECT": "REJECTED"}
        if action not in mapping:
            raise ValueError("INVALID_REVIEW_ACTION")
        result = self.repository.revise_entry(entry_id, content, mapping[action], reviewer, reason)
        case_id = result["case_id"]
        entries = self.repository.entries(case_id)
        if entries and not any(item["status"] in {"PENDING", "MISSING"} for item in entries):
            self.repository.update_case_status(case_id, "ACTIVE")
        return result

    def list_cases(self, **kwargs) -> dict:
        return self.repository.list_cases(**kwargs)

    def detail(self, case_id: str) -> dict | None:
        return self.repository.case_detail(case_id)
