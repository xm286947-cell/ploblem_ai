from __future__ import annotations

from pathlib import Path

import pytest

from quality_knowledge.major_cases.repository import MajorKnowledgeRepository
from repositories import JsonArtifactRepository
from retriever.case_retriever import QueryInput
from services.historical_case_contract import HistoricalCaseConsumerService
from services.major_case_publisher import MajorCasePublisher, PublishCommitError


def _env(tmp_path: Path):
    major = MajorKnowledgeRepository(
        tmp_path / "major.sqlite3",
        tmp_path / "attachments",
    )
    artifacts = JsonArtifactRepository(tmp_path / "published")
    case = major.create_case("控制器重启重大问题", "G1", domain="PLC")
    event = major.upsert_event(
        case["case_id"],
        standard_itr="ITR20260923099",
        internal_event_key="ITR20260923099",
        title="ITR20260923099",
    )
    major.update_case_status(case["case_id"], "ACTIVE")
    return major, artifacts, case, event


def _entry(
    major: MajorKnowledgeRepository,
    case_id: str,
    event_id: str,
    entry_type: str,
    content: str,
    *,
    evidence=(),
):
    return major.add_entry(
        case_id,
        entry_type,
        content,
        assertion_kind="FACT",
        origin="HUMAN",
        status="CONFIRMED",
        event_id=event_id,
        evidence=evidence,
    )


def test_first_publish_created_and_same_revision_reused(tmp_path: Path) -> None:
    major, artifacts, case, event = _env(tmp_path)
    _entry(major, case["case_id"], event["event_id"], "ISSUE_FACT", "控制器周期性重启")
    root = _entry(major, case["case_id"], event["event_id"], "ROOT_CAUSE", "CAN 队列缺少流控")
    _entry(major, case["case_id"], event["event_id"], "ACTION", "增加水位保护")

    publisher = MajorCasePublisher(major, artifacts)
    first = publisher.publish_event(event["event_id"])
    second = publisher.publish_event(event["event_id"])

    assert first["status"] == "CREATED"
    assert first["publication_status"] == "PUBLISHED"
    assert second["status"] == "REUSED"
    assert second["case_id"] == first["case_id"]
    assert second["knowledge_revision"] == first["knowledge_revision"]
    assert root["current_revision_id"] in first["knowledge_revision"]

    case_id = first["case_id"]
    assert artifacts.load(f"knowledge/enriched_case/{case_id}.json")["metadata"]["case_id"] == case_id
    assert artifacts.load(f"knowledge/raw_evidence/{case_id}.json")["case_id"] == case_id
    retrieval = artifacts.load(f"knowledge/retrieval_docs/{case_id}.json")
    assert retrieval["case_id"] == case_id
    assert retrieval["filters"]["knowledge_source"] == "MAJOR_EVENT"


def test_new_major_revision_updates_same_historical_case_id(tmp_path: Path) -> None:
    major, artifacts, case, event = _env(tmp_path)
    issue = _entry(
        major,
        case["case_id"],
        event["event_id"],
        "ISSUE_FACT",
        "原始问题事实",
    )
    _entry(major, case["case_id"], event["event_id"], "ROOT_CAUSE", "根因A")

    publisher = MajorCasePublisher(major, artifacts)
    created = publisher.publish_event(event["event_id"])
    old_revision = created["knowledge_revision"]

    revised = major.revise_entry(
        issue["entry_id"],
        "人工确认后的问题事实",
        "CONFIRMED",
        reviewer="tester",
        reason="事实更新",
    )
    updated = publisher.publish_event(event["event_id"])

    assert updated["status"] == "UPDATED"
    assert updated["case_id"] == created["case_id"]
    assert updated["knowledge_revision"] != old_revision
    assert revised["current_revision_id"] in updated["knowledge_revision"]

    detail = HistoricalCaseConsumerService(artifacts).get_case(updated["case_id"])
    assert detail["problem_description"] == "人工确认后的问题事实"
    assert detail["case_id"] == created["case_id"]


def test_publish_failure_rolls_back_and_never_commits_false_published_state(tmp_path: Path) -> None:
    major, artifacts, case, event = _env(tmp_path)
    issue = _entry(major, case["case_id"], event["event_id"], "ISSUE_FACT", "版本一事实")
    publisher = MajorCasePublisher(major, artifacts)
    created = publisher.publish_event(event["event_id"])
    case_id = created["case_id"]
    before_enriched = artifacts.load(f"knowledge/enriched_case/{case_id}.json")
    before_retrieval = artifacts.load(f"knowledge/retrieval_docs/{case_id}.json")

    major.revise_entry(
        issue["entry_id"],
        "版本二事实",
        "CONFIRMED",
        reviewer="tester",
        reason="更新",
    )

    original_replace = publisher._replace
    state = {"count": 0}

    def fail_once(source, target):
        state["count"] += 1
        if state["count"] == 3:
            raise OSError("synthetic promote failure")
        return original_replace(source, target)

    publisher._replace = fail_once
    with pytest.raises(PublishCommitError, match="PUBLICATION_WRITE_FAILED"):
        publisher.publish_event(event["event_id"])

    assert artifacts.load(f"knowledge/enriched_case/{case_id}.json") == before_enriched
    assert artifacts.load(f"knowledge/retrieval_docs/{case_id}.json") == before_retrieval

    metadata_path = publisher._metadata_path(event["event_id"])
    metadata = artifacts.load(metadata_path)
    assert metadata["publication_status"] == "PUBLISHED"
    assert metadata["knowledge_revision"] == created["knowledge_revision"]

    publisher._replace = original_replace
    retried = publisher.publish_event(event["event_id"])
    assert retried["status"] == "UPDATED"
    assert retried["case_id"] == case_id


def test_first_publish_failure_leaves_no_published_metadata_and_is_retryable(tmp_path: Path) -> None:
    major, artifacts, case, event = _env(tmp_path)
    _entry(major, case["case_id"], event["event_id"], "ISSUE_FACT", "首次发布事实")
    publisher = MajorCasePublisher(major, artifacts)

    original_replace = publisher._replace
    state = {"count": 0}

    def fail_once(source, target):
        state["count"] += 1
        if state["count"] == 2:
            raise OSError("synthetic first publish failure")
        return original_replace(source, target)

    publisher._replace = fail_once
    with pytest.raises(PublishCommitError, match="PUBLICATION_WRITE_FAILED"):
        publisher.publish_event(event["event_id"])

    assert artifacts.load(publisher._metadata_path(event["event_id"])) is None

    publisher._replace = original_replace
    retried = publisher.publish_event(event["event_id"])
    assert retried["status"] == "CREATED"
    assert retried["publication_status"] == "PUBLISHED"


def test_publish_search_detail_evidence_roundtrip_uses_consumer_contract_only(tmp_path: Path) -> None:
    major, artifacts, case, event = _env(tmp_path)
    link = major.add_source_link(
        case["case_id"],
        event["event_id"],
        {
            "record_id": "ROW-99",
            "source_type": "ITR",
            "source_system": "BUSINESS_DB",
            "group_code": "G1",
        },
        standard_itr=event["standard_itr"],
        role="CURRENT_EVENT",
        status="LINKED",
    )
    _entry(
        major,
        case["case_id"],
        event["event_id"],
        "ISSUE_FACT",
        "控制器周期性重启",
        evidence=[{
            "source_link_id": link["source_link_id"],
            "locator": "problem",
            "excerpt": "原始记录：控制器周期性重启",
        }],
    )
    _entry(
        major,
        case["case_id"],
        event["event_id"],
        "ROOT_CAUSE",
        "CAN 队列缺少流控",
        evidence=[{
            "source_link_id": link["source_link_id"],
            "locator": "root_cause",
            "excerpt": "原始记录：CAN 队列缺少流控",
        }],
    )
    _entry(major, case["case_id"], event["event_id"], "ACTION", "增加队列水位保护")

    published = MajorCasePublisher(major, artifacts).publish_event(event["event_id"])

    def repeat_search(query: QueryInput, top_k: int | None):
        docs = artifacts.list("knowledge/retrieval_docs")
        results = []
        for rank, path in enumerate(docs, start=1):
            doc = artifacts.load(path)
            if not doc:
                continue
            if query.text in doc["text"] or "控制器" in doc["text"]:
                results.append({
                    "case_id": doc["case_id"],
                    "title": doc["title"],
                    "summary": doc["title"],
                    "score": 1.0,
                    "rank": rank,
                    "reasons": ["发布后检索命中"],
                })
        return {"results": results[: top_k or len(results)]}

    consumer = HistoricalCaseConsumerService(
        artifacts,
        repeat_search=repeat_search,
    )
    search = consumer.search_repeat_cases(
        QueryInput(text="控制器周期性重启"),
        top_k=10,
    )
    assert search["contract_version"] == "historical-case/v1"
    assert search["candidates"][0]["case_id"] == published["case_id"]

    detail = consumer.get_case(search["candidates"][0]["case_id"])
    assert detail["case_id"] == published["case_id"]
    assert detail["problem_description"] == "控制器周期性重启"
    assert detail["root_cause"] == "CAN 队列缺少流控"
    assert detail["solution"] == "增加队列水位保护"
    assert detail["evidence"]
    assert detail["evidence"][0]["raw_text"].startswith("原始记录")
    assert "internal_path" not in repr(search)
    assert "internal_path" not in repr(detail)
    assert str(major.db_path) not in repr(search)
    assert str(major.db_path) not in repr(detail)


def test_missing_root_cause_and_action_survive_roundtrip_as_null(tmp_path: Path) -> None:
    major, artifacts, case, event = _env(tmp_path)
    _entry(major, case["case_id"], event["event_id"], "ISSUE_FACT", "只有问题事实")

    published = MajorCasePublisher(major, artifacts).publish_event(event["event_id"])
    detail = HistoricalCaseConsumerService(artifacts).get_case(published["case_id"])

    assert detail["root_cause"] is None
    assert detail["solution"] is None
    assert "ROOT_CAUSE_MISSING" in published["validation_warnings"]
    assert "ACTION_MISSING" in published["validation_warnings"]
