from __future__ import annotations

from pathlib import Path

import pytest

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
    return repo.add_entry(
        case_id,
        entry_type,
        content,
        assertion_kind="FACT",
        origin="HUMAN",
        status=status,
        event_id=event_id,
        evidence=evidence or [],
    )


def test_ac01_ac02_ac06_to_ac09_confirmed_fields_are_mapped(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    case, event = _active_case_event(repo, title="控制器异常复盘")
    _entry(repo, case["case_id"], "ISSUE_FACT", "控制器周期性重启", event_id=event["event_id"])
    _entry(repo, case["case_id"], "ROOT_CAUSE", "CAN 队列缺少流控", event_id=event["event_id"])
    _entry(repo, case["case_id"], "ACTION", "增加队列水位保护", event_id=event["event_id"])
    _entry(repo, case["case_id"], "VERIFICATION", "回归测试通过", event_id=event["event_id"])
    _entry(repo, case["case_id"], "ROOT_CAUSE", "待确认根因", event_id=event["event_id"], status="PENDING")
    _entry(repo, case["case_id"], "ACTION", "已更正但本契约不发布", event_id=event["event_id"], status="CORRECTED")
    _entry(repo, case["case_id"], "OTHER", "非发布类型", event_id=event["event_id"])

    candidate = MajorCasePublishAdapter(repo).build_candidate(event["event_id"])

    assert candidate["source_identity"] == {
        "source_type": "MAJOR_EVENT",
        "source_id": event["event_id"],
        "business_id": "ITR20260923001",
    }
    assert candidate["title"] == "控制器异常复盘"
    assert candidate["enriched_case"]["problem"]["standard_description"] == "控制器周期性重启"
    assert candidate["enriched_case"]["problem"]["phenomenon"] == [{"value": "控制器周期性重启"}]
    assert candidate["enriched_case"]["analysis"]["root_cause"] == [{"value": "CAN 队列缺少流控"}]
    assert candidate["enriched_case"]["solution"]["corrective_actions"] == [{"value": "增加队列水位保护"}]
    assert candidate["enriched_case"]["solution"]["verification_result"] == "回归测试通过"
    serialized = repr(candidate)
    assert "待确认根因" not in serialized
    assert "已更正但本契约不发布" not in serialized
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
    _entry(repo, case["case_id"], "ROOT_CAUSE", "事件A根因", event_id=event_a["event_id"])
    _entry(repo, case["case_id"], "ISSUE_FACT", "事件B事实", event_id=event_b["event_id"])
    _entry(repo, case["case_id"], "ROOT_CAUSE", "事件B根因", event_id=event_b["event_id"])
    shared = _entry(repo, case["case_id"], "VERIFICATION", "共享验证结论")
    repo.set_entry_scope(shared["entry_id"], scope="CASE_SHARED")
    _entry(repo, case["case_id"], "ACTION", "未定域措施")

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
    _entry(
        repo,
        case["case_id"],
        "ROOT_CAUSE",
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
    assert sections[0]["source_id"] == event["standard_itr"]
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
    with pytest.raises(PublishValidationError, match="NO_PUBLISHABLE_CONFIRMED_FACT") as empty:
        adapter.build_candidate(empty_event["event_id"])
    assert empty.value.code == "NO_PUBLISHABLE_CONFIRMED_FACT"


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
