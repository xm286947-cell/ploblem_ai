from __future__ import annotations

from pathlib import Path

import pytest

from quality_knowledge.major_cases.document_parser import ParseResult, ParsedFragment
from quality_knowledge.major_cases.repository import MajorKnowledgeRepository
from services.major_case_publish import MajorCasePublishAdapter, PublishValidationError


def _repo(tmp_path: Path) -> MajorKnowledgeRepository:
    return MajorKnowledgeRepository(
        tmp_path / "knowledge.sqlite3",
        tmp_path / "attachments",
    )


def _active_case_event(
    repo: MajorKnowledgeRepository,
    *,
    title: str = "重大问题复盘",
    itr: str = "ITR20260923001",
) -> tuple[dict, dict]:
    case = repo.create_case(title, "G1", domain="PLC")
    event = repo.upsert_event(
        case["case_id"],
        standard_itr=itr,
        internal_event_key=itr or f"{case['case_id']}:EVENT",
        title=itr or "内部事件",
    )
    repo.update_case_status(case["case_id"], "ACTIVE")
    return repo.get_case(case["case_id"]) or case, event


def _entry(
    repo: MajorKnowledgeRepository,
    case_id: str,
    entry_type: str,
    content: str,
    *,
    event_id: str | None = None,
    status: str = "CONFIRMED",
    evidence: list[dict] | None = None,
) -> dict:
    event = repo.event(event_id) if event_id else None
    link = repo.add_source_link(
        case_id,
        event_id,
        {
            "record_id": f"TEST-{entry_type}-{event_id or 'SHARED'}",
            "source_type": "ITR",
            "source_system": "TEST",
        },
        standard_itr=(event or {}).get("standard_itr") or "",
        role="CURRENT_EVENT",
        status="LINKED",
    )
    evidence_refs = evidence or [{
        "source_link_id": link["source_link_id"],
        "locator": entry_type.lower(),
        "excerpt": content,
    }]
    if status == "MISSING":
        return repo.add_entry(
            case_id,
            entry_type,
            "",
            assertion_kind="UNKNOWN",
            origin="SOURCE_FUSION",
            status="MISSING",
            event_id=event_id,
        )
    pending = repo.add_entry(
        case_id,
        entry_type,
        content,
        assertion_kind="FACT",
        origin="SOURCE_FUSION",
        status="PENDING",
        event_id=event_id,
        evidence=evidence_refs,
    )
    if status in {"CONFIRMED", "CORRECTED"}:
        return repo.revise_entry(
            pending["entry_id"], content, status, "test-reviewer", "reviewed"
        )
    return pending


def test_i3_all_typed_fields_are_mapped_and_legacy_types_are_not_authority(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    case, event = _active_case_event(repo, title="控制器异常复盘")
    _entry(repo, case["case_id"], "ISSUE_FACT", "控制器周期性重启", event_id=event["event_id"])
    typed_values = {
        "TRC_OCCURRENCE": "TRC 发生",
        "TRC_ESCAPE": "TRC 流出",
        "MRC_OCCURRENCE": "MRC 发生",
        "MRC_ESCAPE": "MRC 流出",
        "TECHNICAL_ACTION": "技术措施",
        "MANAGEMENT_ACTION": "管理措施",
        "CORRECTIVE_ACTION": "纠正措施",
        "PREVENTIVE_ACTION": "预防措施",
    }
    created = {
        entry_type: _entry(
            repo, case["case_id"], entry_type, content, event_id=event["event_id"]
        )
        for entry_type, content in typed_values.items()
    }
    repo.revise_entry(
        created["CORRECTIVE_ACTION"]["entry_id"],
        "已更正的纠正措施",
        "CORRECTED",
        "test-reviewer",
        "更正依据",
    )
    _entry(repo, case["case_id"], "VERIFICATION", "回归测试通过", event_id=event["event_id"])
    _entry(repo, case["case_id"], "ROOT_CAUSE", "不再是发布权威", event_id=event["event_id"])
    _entry(repo, case["case_id"], "ACTION", "不再是发布权威", event_id=event["event_id"])
    _entry(repo, case["case_id"], "OTHER", "非发布类型", event_id=event["event_id"])

    candidate = MajorCasePublishAdapter(repo).build_candidate(event["event_id"])

    assert candidate["source_identity"] == {
        "source_type": "MAJOR_EVENT",
        "source_id": event["event_id"],
        "business_id": "ITR20260923001",
    }
    assert candidate["title"] == "控制器异常复盘"
    assert candidate["enriched_case"]["problem"]["standard_description"] == "控制器周期性重启"
    assert candidate["enriched_case"]["problem"]["phenomenon"][0]["value"] == "控制器周期性重启"
    analysis = candidate["enriched_case"]["analysis"]
    assert analysis["trc"]["occurrence"]["standard"] == "TRC 发生"
    assert analysis["trc"]["escape"]["standard"] == "TRC 流出"
    assert analysis["mrc"]["occurrence"]["standard"] == "MRC 发生"
    assert analysis["mrc"]["escape"]["standard"] == "MRC 流出"
    solution = candidate["enriched_case"]["solution"]
    assert solution["technical_actions"][0]["value"] == "技术措施"
    assert solution["management_actions"][0]["value"] == "管理措施"
    assert solution["corrective_actions"][0]["value"] == "已更正的纠正措施"
    assert solution["preventive_actions"][0]["value"] == "预防措施"
    assert analysis["root_cause"] == [{
        "value": "TRC occurrence: TRC 发生\nMRC occurrence: MRC 发生"
    }]
    assert candidate["enriched_case"]["solution"]["verification_result"] == "回归测试通过"
    assert candidate["enriched_case"]["metadata"]["semantic_projection_contract"] == "major-semantic-publish/v1"
    assert "不再是发布权威" not in repr(candidate)
    serialized = repr(candidate)
    assert "非发布类型" not in serialized


def test_ac03_ac04_ac05_event_isolation_shared_and_unscoped_protection(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    case = repo.create_case("多 ITR 复盘", "G1")
    event_a = repo.upsert_event(
        case["case_id"],
        standard_itr="ITR-A",
        internal_event_key="ITR-A",
        title="ITR-A",
    )
    event_b = repo.upsert_event(
        case["case_id"],
        standard_itr="ITR-B",
        internal_event_key="ITR-B",
        title="ITR-B",
    )
    repo.update_case_status(case["case_id"], "ACTIVE")

    _entry(repo, case["case_id"], "ISSUE_FACT", "事件A事实", event_id=event_a["event_id"])
    _entry(repo, case["case_id"], "TRC_OCCURRENCE", "事件A根因", event_id=event_a["event_id"])
    _entry(repo, case["case_id"], "ISSUE_FACT", "事件B事实", event_id=event_b["event_id"])
    _entry(repo, case["case_id"], "TRC_OCCURRENCE", "事件B根因", event_id=event_b["event_id"])
    shared = _entry(repo, case["case_id"], "VERIFICATION", "共享验证结论")
    repo.set_entry_scope(shared["entry_id"], scope="CASE_SHARED")
    _entry(repo, case["case_id"], "CORRECTIVE_ACTION", "未定域措施")

    adapter = MajorCasePublishAdapter(repo)
    candidate_a = adapter.build_candidate(event_a["event_id"])
    candidate_b = adapter.build_candidate(event_b["event_id"])

    raw_a = repr(candidate_a)
    raw_b = repr(candidate_b)
    assert "事件A事实" in raw_a and "事件A根因" in raw_a
    assert "事件B事实" not in raw_a and "事件B根因" not in raw_a
    assert "事件B事实" in raw_b and "事件B根因" in raw_b
    assert "事件A事实" not in raw_b and "事件A根因" not in raw_b
    assert "共享验证结论" in raw_a and "共享验证结论" in raw_b
    assert "未定域措施" not in raw_a and "未定域措施" not in raw_b
    assert "UNSCOPED_KNOWLEDGE_EXCLUDED" in candidate_a["validation_warnings"]
    assert "UNSCOPED_KNOWLEDGE_EXCLUDED" in candidate_b["validation_warnings"]


def test_ac10_ac11_missing_root_cause_or_action_is_valid_with_warnings(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    case, event = _active_case_event(repo)
    _entry(repo, case["case_id"], "ISSUE_FACT", "仅确认问题事实", event_id=event["event_id"])

    candidate = MajorCasePublishAdapter(repo).build_candidate(event["event_id"])

    assert candidate["enriched_case"]["analysis"]["root_cause"] == []
    assert candidate["enriched_case"]["solution"]["corrective_actions"] == []
    assert "ROOT_CAUSE_MISSING" in candidate["validation_warnings"]
    assert "ACTION_MISSING" in candidate["validation_warnings"]


def test_valid_missing_typed_slot_stays_empty_without_blocking_other_reviewed_slots(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    case, event = _active_case_event(repo)
    _entry(repo, case["case_id"], "TRC_OCCURRENCE", "已确认的发生原因", event_id=event["event_id"])
    _entry(repo, case["case_id"], "TRC_ESCAPE", "", event_id=event["event_id"], status="MISSING")

    candidate = MajorCasePublishAdapter(repo).build_candidate(event["event_id"])

    assert candidate["enriched_case"]["analysis"]["trc"]["occurrence"]["standard"] == "已确认的发生原因"
    assert candidate["enriched_case"]["analysis"]["trc"]["escape"] == {
        "original": "", "report": "", "standard": "", "confidence": 0.0, "evidence_refs": []
    }
    assert "SEMANTIC_SLOT_MISSING:TRC_ESCAPE" in candidate["validation_warnings"]


def test_pending_formal_typed_review_blocks_publication(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    case, event = _active_case_event(repo)
    _entry(repo, case["case_id"], "ISSUE_FACT", "已审核问题事实", event_id=event["event_id"])
    _entry(repo, case["case_id"], "TRC_OCCURRENCE", "来源有待裁决", event_id=event["event_id"], status="PENDING")

    with pytest.raises(PublishValidationError, match="PUBLISH_REVIEW_REQUIRED"):
        MajorCasePublishAdapter(repo).build_candidate(event["event_id"])


def test_ai_status_cannot_claim_human_authority_and_malformed_missing_fails_closed(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    case, event = _active_case_event(repo)
    link = repo.add_source_link(
        case["case_id"], event["event_id"],
        {"record_id": "ROW-AI", "source_type": "ITR", "source_system": "TEST"},
        standard_itr=event["standard_itr"], role="CURRENT_EVENT", status="LINKED",
    )
    repo.add_entry(
        case["case_id"], "TRC_OCCURRENCE", "AI 不能冒充人工确认",
        assertion_kind="FACT", origin="AI", status="CONFIRMED", event_id=event["event_id"],
        evidence=[{"source_link_id": link["source_link_id"], "locator": "row:A", "excerpt": "AI 来源"}],
    )
    with pytest.raises(PublishValidationError, match="PUBLISH_REVISION_NOT_HUMAN"):
        MajorCasePublishAdapter(repo).build_candidate(event["event_id"])

    missing_case, missing_event = _active_case_event(repo, itr="ITR-BAD-MISSING")
    missing_link = repo.add_source_link(
        missing_case["case_id"], missing_event["event_id"],
        {"record_id": "ROW-MISSING", "source_type": "ITR", "source_system": "TEST"},
        standard_itr=missing_event["standard_itr"], role="CURRENT_EVENT", status="LINKED",
    )
    repo.add_entry(
        missing_case["case_id"], "TRC_ESCAPE", "不合法的 MISSING 内容",
        assertion_kind="UNKNOWN", origin="SOURCE_FUSION", status="MISSING",
        event_id=missing_event["event_id"], evidence=[{
            "source_link_id": missing_link["source_link_id"], "locator": "row:missing", "excerpt": "不得附加"
        }],
    )
    with pytest.raises(PublishValidationError, match="PUBLISH_SEMANTIC_CONTENT_INVALID"):
        MajorCasePublishAdapter(repo).build_candidate(missing_event["event_id"])


def test_rejected_and_unscoped_revisions_never_become_publish_authority(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    case, event = _active_case_event(repo)
    unscoped_link = repo.add_source_link(
        case["case_id"], None,
        {"record_id": "ROW-UNSCOPED", "source_type": "ITR", "source_system": "TEST"},
        standard_itr="", role="CURRENT_EVENT", status="LINKED",
    )
    repo.add_entry(
        case["case_id"], "TRC_OCCURRENCE", "未定域结论不能发布",
        assertion_kind="HUMAN_REVISION", origin="HUMAN", status="CONFIRMED", evidence=[{
            "source_link_id": unscoped_link["source_link_id"], "locator": "row:shared?", "excerpt": "未定域来源"
        }],
    )
    rejected_link = repo.add_source_link(
        case["case_id"], event["event_id"],
        {"record_id": "ROW-REJECTED", "source_type": "ITR", "source_system": "TEST"},
        standard_itr=event["standard_itr"], role="CURRENT_EVENT", status="LINKED",
    )
    repo.add_entry(
        case["case_id"], "CORRECTIVE_ACTION", "已驳回措施",
        assertion_kind="HUMAN_REVISION", origin="HUMAN", status="REJECTED",
        event_id=event["event_id"], evidence=[{
            "source_link_id": rejected_link["source_link_id"], "locator": "row:rejected", "excerpt": "驳回来源"
        }],
    )

    with pytest.raises(PublishValidationError, match="NO_PUBLISHABLE_HUMAN_REVISION"):
        MajorCasePublishAdapter(repo).build_candidate(event["event_id"])


def test_multiple_typed_actions_keep_their_own_evidence_binding(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    case, event = _active_case_event(repo)
    first = _entry(repo, case["case_id"], "CORRECTIVE_ACTION", "相同措施", event_id=event["event_id"])
    second = _entry(repo, case["case_id"], "CORRECTIVE_ACTION", "相同措施", event_id=event["event_id"])

    candidate = MajorCasePublishAdapter(repo).build_candidate(event["event_id"])
    actions = candidate["enriched_case"]["solution"]["corrective_actions"]
    evidence_by_id = {
        section["evidence_id"]: section
        for section in candidate["raw_evidence"]["sections"]
    }

    assert [item["value"] for item in actions] == ["相同措施", "相同措施"]
    for action, entry in zip(actions, (first, second), strict=True):
        ref = action["evidence_refs"][0]
        raw = evidence_by_id[ref["source_location"].removeprefix("evidence://")]
        assert raw["entry_id"] == entry["entry_id"]
        assert ref["quote"] == entry["evidence"][0]["excerpt"]


def test_excel_pdf_projection_preserves_both_source_lineages(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    case, event = _active_case_event(repo)
    excel_link = repo.add_source_link(
        case["case_id"], None,
        {
            "record_id": "KFACT-REV-8",
            "source_type": "MAJOR_EXCEL_SOURCE_FACT",
            "source_system": "MAJOR_EXCEL_IMPORT",
            "source_hash": "excel-hash",
            "source_ref": "Excel!A8",
        },
        standard_itr="", role="CURRENT_EVENT", status="LINKED",
    )
    report_path = tmp_path / "report.pdf"
    report_path.write_bytes(b"test-only-placeholder")
    document = repo.ingest_file(case["case_id"], report_path)
    fragment = repo.save_parse_result(
        document["version_id"],
        ParseResult("PDF", [ParsedFragment(1, "root-cause", "PAGE", "page:4", "TEXT", "PDF 细化结论")]),
    )[0]
    pdf_link = repo.add_source_link(
        case["case_id"], event["event_id"],
        {
            "record_id": document["version_id"],
            "source_type": "MAJOR_SOURCE_DOCUMENT",
            "source_system": "MAJOR_SOURCE_INTAKE",
            "file_name": document["original_filename"],
            "version_id": document["version_id"],
            "document_id": document["document_id"],
        },
        standard_itr=event["standard_itr"], role="CURRENT_EVENT", status="LINKED",
    )
    pending = repo.add_entry(
        case["case_id"], "TRC_OCCURRENCE", "人工归一后的发生原因",
        assertion_kind="FACT", origin="SOURCE_FUSION", status="PENDING",
        event_id=event["event_id"], evidence=[
            {"source_link_id": excel_link["source_link_id"], "locator": "Excel!F8", "excerpt": "Excel 原文"},
            {
                "source_link_id": pdf_link["source_link_id"],
                "fragment_id": fragment["fragment_id"],
                "locator": "page:4/section:root-cause",
                "excerpt": "PDF 细化结论",
            },
        ],
    )
    reviewed = repo.revise_entry(
        pending["entry_id"], "人工归一后的发生原因", "CONFIRMED", "reviewer", "已比较两类来源"
    )

    candidate = MajorCasePublishAdapter(repo).build_candidate(event["event_id"])
    detail = candidate["enriched_case"]["analysis"]["trc"]["occurrence"]
    sections = candidate["raw_evidence"]["sections"]

    assert detail["original"] == "Excel 原文"
    assert detail["report"] == "PDF 细化结论"
    assert {item["source_type"] for item in detail["evidence_refs"]} == {"EXCEL", "PDF"}
    assert {item["source_modality"] for item in sections} == {"EXCEL", "PDF"}
    assert {item["origin_source_id"] for item in sections} == {
        "KFACT-REV-8", document["version_id"]
    }
    assert {item["revision_id"] for item in sections} == {reviewed["current_revision_id"]}
    assert {item["locator"] for item in sections} == {"Excel!F8", "page:4/section:root-cause"}
    pdf_section = next(item for item in sections if item["source_modality"] == "PDF")
    assert pdf_section["fragment_id"] == fragment["fragment_id"]
    assert pdf_section["origin_source_version"] == document["version_id"]
    assert pdf_section["page"] == 4
    assert pdf_section["source_link_id"] == pdf_link["source_link_id"]


def test_wrong_event_and_missing_source_lineage_fail_closed(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    case, event_a = _active_case_event(repo, itr="ITR-A")
    event_b = repo.upsert_event(case["case_id"], standard_itr="ITR-B", internal_event_key="ITR-B")
    link_b = repo.add_source_link(
        case["case_id"], event_b["event_id"],
        {"record_id": "ROW-B", "source_type": "ITR", "source_system": "TEST"},
        standard_itr=event_b["standard_itr"], role="CURRENT_EVENT", status="LINKED",
    )
    pending = repo.add_entry(
        case["case_id"], "TRC_OCCURRENCE", "事件 A 的人工结论",
        assertion_kind="FACT", origin="SOURCE_FUSION", status="PENDING",
        event_id=event_a["event_id"], evidence=[{
            "source_link_id": link_b["source_link_id"], "locator": "row:B", "excerpt": "事件 B 来源"
        }],
    )
    repo.revise_entry(pending["entry_id"], "事件 A 的人工结论", "CONFIRMED", "reviewer", "审核")

    with pytest.raises(PublishValidationError, match="PUBLISH_EVIDENCE_EVENT_MISMATCH"):
        MajorCasePublishAdapter(repo).build_candidate(event_a["event_id"])

    missing_case, missing_event = _active_case_event(repo, itr="ITR-MISSING-LINK")
    missing_entry = _entry(
        repo, missing_case["case_id"], "TRC_OCCURRENCE", "来源链接不可见",
        event_id=missing_event["event_id"],
    )
    original_source_links = repo.source_links
    repo.source_links = lambda case_id: (
        [] if case_id == missing_case["case_id"] else original_source_links(case_id)
    )
    with pytest.raises(PublishValidationError, match="PUBLISH_EVIDENCE_SOURCE_NOT_FOUND"):
        MajorCasePublishAdapter(repo).build_candidate(missing_event["event_id"])


def test_fragment_requires_resolvable_source_link_and_exact_document_version(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    missing_case, missing_event = _active_case_event(repo, itr="ITR-NO-SOURCE-LINK")
    missing_pdf = tmp_path / "unlinked.pdf"
    missing_pdf.write_bytes(b"test-only-placeholder")
    missing_document = repo.ingest_file(missing_case["case_id"], missing_pdf)
    missing_fragment = repo.save_parse_result(
        missing_document["version_id"],
        ParseResult("PDF", [ParsedFragment(1, "cause", "PAGE", "4", "TEXT", "来源片段")]),
    )[0]
    missing_pending = repo.add_entry(
        missing_case["case_id"], "TRC_OCCURRENCE", "人工确认的原因",
        assertion_kind="FACT", origin="SOURCE_FUSION", status="PENDING",
        event_id=missing_event["event_id"], evidence=[{
            "fragment_id": missing_fragment["fragment_id"],
            "locator": "page:4", "excerpt": "来源片段",
        }],
    )
    repo.revise_entry(
        missing_pending["entry_id"], "人工确认的原因", "CONFIRMED", "reviewer", "审核"
    )

    with pytest.raises(PublishValidationError, match="PUBLISH_EVIDENCE_SOURCE_REQUIRED"):
        MajorCasePublishAdapter(repo).build_candidate(missing_event["event_id"])

    mismatch_case, mismatch_event = _active_case_event(repo, itr="ITR-WRONG-DOCUMENT")
    linked_pdf = tmp_path / "linked.pdf"
    linked_pdf.write_bytes(b"linked-placeholder")
    linked_document = repo.ingest_file(mismatch_case["case_id"], linked_pdf)
    foreign_pdf = tmp_path / "foreign.pdf"
    foreign_pdf.write_bytes(b"foreign-placeholder")
    foreign_document = repo.ingest_file(mismatch_case["case_id"], foreign_pdf)
    foreign_fragment = repo.save_parse_result(
        foreign_document["version_id"],
        ParseResult("PDF", [ParsedFragment(1, "cause", "PAGE", "4", "TEXT", "另一文档片段")]),
    )[0]
    linked_source = repo.add_source_link(
        mismatch_case["case_id"], mismatch_event["event_id"],
        {
            "record_id": linked_document["version_id"],
            "source_type": "MAJOR_SOURCE_DOCUMENT",
            "source_system": "TEST",
            "version_id": linked_document["version_id"],
        },
        standard_itr=mismatch_event["standard_itr"],
        role="CURRENT_EVENT", status="LINKED",
    )
    mismatch_pending = repo.add_entry(
        mismatch_case["case_id"], "TRC_OCCURRENCE", "人工确认的原因",
        assertion_kind="FACT", origin="SOURCE_FUSION", status="PENDING",
        event_id=mismatch_event["event_id"], evidence=[{
            "source_link_id": linked_source["source_link_id"],
            "fragment_id": foreign_fragment["fragment_id"],
            "locator": "page:4", "excerpt": "另一文档片段",
        }],
    )
    repo.revise_entry(
        mismatch_pending["entry_id"], "人工确认的原因", "CONFIRMED", "reviewer", "审核"
    )

    with pytest.raises(PublishValidationError, match="PUBLISH_EVIDENCE_SOURCE_MISMATCH"):
        MajorCasePublishAdapter(repo).build_candidate(mismatch_event["event_id"])

    resolved_case, resolved_event = _active_case_event(repo, itr="ITR-EXACT-LINK")
    resolved_pdf = tmp_path / "resolvable.pdf"
    resolved_pdf.write_bytes(b"resolvable-placeholder")
    resolved_document = repo.ingest_file(resolved_case["case_id"], resolved_pdf)
    resolved_fragment = repo.save_parse_result(
        resolved_document["version_id"],
        ParseResult("PDF", [ParsedFragment(1, "cause", "PAGE", "4", "TEXT", "精确来源片段")]),
    )[0]
    exact_source = repo.add_source_link(
        resolved_case["case_id"], resolved_event["event_id"],
        {
            "record_id": resolved_document["version_id"],
            "source_type": "MAJOR_SOURCE_DOCUMENT",
            "source_system": "TEST",
            "version_id": resolved_document["version_id"],
        },
        standard_itr=resolved_event["standard_itr"],
        role="CURRENT_EVENT", status="LINKED",
    )
    resolved_pending = repo.add_entry(
        resolved_case["case_id"], "TRC_OCCURRENCE", "人工确认的原因",
        assertion_kind="FACT", origin="SOURCE_FUSION", status="PENDING",
        event_id=resolved_event["event_id"], evidence=[{
            "fragment_id": resolved_fragment["fragment_id"],
            "locator": "page:4", "excerpt": "精确来源片段",
        }],
    )
    repo.revise_entry(
        resolved_pending["entry_id"], "人工确认的原因", "CONFIRMED", "reviewer", "审核"
    )

    resolved = MajorCasePublishAdapter(repo).build_candidate(resolved_event["event_id"])
    section = resolved["raw_evidence"]["sections"][0]
    assert section["source_link_id"] == exact_source["source_link_id"]
    assert section["origin_source_id"] == resolved_document["version_id"]
    assert section["origin_source_version"] == resolved_document["version_id"]


def test_eventless_source_link_with_other_itr_cannot_cross_event(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    case, event_a = _active_case_event(repo, itr="ITR-A")
    event_b = repo.upsert_event(case["case_id"], standard_itr="ITR-B", internal_event_key="ITR-B")
    shared_link = repo.add_source_link(
        case["case_id"], None,
        {"record_id": "ROW-B-SHARED", "source_type": "ITR", "source_system": "TEST"},
        standard_itr=event_b["standard_itr"], role="CURRENT_EVENT", status="LINKED",
    )
    pending = repo.add_entry(
        case["case_id"], "TRC_OCCURRENCE", "事件 A 的人工结论",
        assertion_kind="FACT", origin="SOURCE_FUSION", status="PENDING",
        event_id=event_a["event_id"], evidence=[{
            "source_link_id": shared_link["source_link_id"],
            "locator": "row:B", "excerpt": "事件 B 来源",
        }],
    )
    repo.revise_entry(pending["entry_id"], "事件 A 的人工结论", "CONFIRMED", "reviewer", "审核")

    with pytest.raises(PublishValidationError, match="PUBLISH_EVIDENCE_EVENT_MISMATCH"):
        MajorCasePublishAdapter(repo).build_candidate(event_a["event_id"])


def test_ac12_evidence_preserves_provenance_without_guessing(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    case, event = _active_case_event(repo)
    link = repo.add_source_link(
        case["case_id"],
        event["event_id"],
        {
            "record_id": "ITR-ROW-1",
            "source_type": "ITR",
            "source_system": "BUSINESS_DB",
            "group_code": "G1",
        },
        standard_itr=event["standard_itr"],
        role="CURRENT_EVENT",
        status="LINKED",
    )
    root_entry = _entry(
        repo,
        case["case_id"],
        "TRC_OCCURRENCE",
        "人工确认根因",
        event_id=event["event_id"],
        evidence=[
            {
                "source_link_id": link["source_link_id"],
                "locator": "root-cause",
                "excerpt": "原始根因段落",
            }
        ],
    )

    candidate = MajorCasePublishAdapter(repo).build_candidate(event["event_id"])
    sections = candidate["raw_evidence"]["sections"]

    assert len(sections) == 1
    assert sections[0]["raw_text"] == "原始根因段落"
    assert sections[0]["section"] == "root-cause"
    assert sections[0]["source_type"] == "ITR"
    assert sections[0]["source_modality"] is None
    assert sections[0]["source_id"] == event["standard_itr"]
    assert sections[0]["evidence_id"].startswith("MJR-EVD-")
    assert sections[0]["source_version"] == root_entry["current_revision_id"]
    assert sections[0]["source_ref"] == (
        f"ITR:{event['standard_itr']}@{root_entry['current_revision_id']}"
    )
    assert sections[0]["source_link_id"] == link["source_link_id"]
    assert sections[0]["origin_source_id"] == "ITR-ROW-1"
    assert sections[0]["page"] is None
    assert sections[0]["page_numbers"] == []
    assert sections[0]["file_name"] is None
    assert sections[0]["url"] is None
    assert candidate["raw_evidence"]["report_filename"] is None


def test_validation_rejects_missing_inactive_or_empty_event(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    adapter = MajorCasePublishAdapter(repo)

    with pytest.raises(PublishValidationError, match="EVENT_NOT_FOUND") as missing:
        adapter.build_candidate("KEVT-MISSING")
    assert missing.value.code == "EVENT_NOT_FOUND"

    case = repo.create_case("未激活案例", "G1")
    event = repo.upsert_event(
        case["case_id"],
        standard_itr="ITR-INACTIVE",
        internal_event_key="ITR-INACTIVE",
    )
    _entry(repo, case["case_id"], "ISSUE_FACT", "事实", event_id=event["event_id"])
    with pytest.raises(PublishValidationError, match="MAJOR_CASE_NOT_ACTIVE") as inactive:
        adapter.build_candidate(event["event_id"])
    assert inactive.value.code == "MAJOR_CASE_NOT_ACTIVE"

    repo.update_case_status(case["case_id"], "ACTIVE")
    empty_case = repo.create_case("无可发布事实", "G1")
    empty_event = repo.upsert_event(
        empty_case["case_id"],
        standard_itr="ITR-EMPTY",
        internal_event_key="ITR-EMPTY",
    )
    repo.update_case_status(empty_case["case_id"], "ACTIVE")
    _entry(
        repo,
        empty_case["case_id"],
        "ISSUE_FACT",
        "仍未确认",
        event_id=empty_event["event_id"],
        status="PENDING",
    )
    with pytest.raises(PublishValidationError, match="PUBLISH_REVIEW_REQUIRED") as empty:
        adapter.build_candidate(empty_event["event_id"])
    assert empty.value.code == "PUBLISH_REVIEW_REQUIRED"


def test_validation_rejects_event_case_and_publication_identity_conflicts(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    case, event = _active_case_event(repo)
    _entry(repo, case["case_id"], "ISSUE_FACT", "事实", event_id=event["event_id"])
    adapter = MajorCasePublishAdapter(repo)

    with pytest.raises(PublishValidationError, match="EVENT_CASE_MISMATCH"):
        adapter.build_candidate(event["event_id"], expected_case_id="KCASE-OTHER")

    with pytest.raises(PublishValidationError, match="PUBLICATION_IDENTITY_CONFLICT"):
        adapter.build_candidate(
            event["event_id"],
            existing_mapping={
                "source_type": "MAJOR_EVENT",
                "source_id": "KEVT-OTHER",
                "business_id": event["standard_itr"],
            },
        )


def test_knowledge_revision_reuses_current_major_entry_revisions(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    case, event = _active_case_event(repo)
    entry = _entry(
        repo,
        case["case_id"],
        "ISSUE_FACT",
        "原始确认事实",
        event_id=event["event_id"],
    )
    adapter = MajorCasePublishAdapter(repo)

    before = adapter.build_candidate(event["event_id"])
    before_revision = before["knowledge_revision"]
    assert entry["current_revision_id"] in before_revision

    revised = repo.revise_entry(
        entry["entry_id"],
        "人工修订后的确认事实",
        "CONFIRMED",
        reviewer="tester",
        reason="事实修订",
    )
    after = adapter.build_candidate(event["event_id"])
    assert after["knowledge_revision"] != before_revision
    assert revised["current_revision_id"] in after["knowledge_revision"]
    assert "人工修订后的确认事实" in repr(after)
