"""GOLDEN-E2E-001: repository synthetic fixture, never production evidence.

The fixture values are reused from test_repeat_web_mvp.py and
test_case_publish_service.py.  Search uses the existing Historical Case
consumer contract with a deterministic retrieval adapter over published docs.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from quality_knowledge.major_cases.repository import MajorKnowledgeRepository
from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.p0.repository import P0Repository
from quality_knowledge.repeat_risk import RepeatQueryTraceRepository
from quality_knowledge.web.p0_app import create_p0_app
from quality_knowledge.web.repeat_risk_integration import RepeatWebFacade
from repositories import JsonArtifactRepository
from services.historical_case_contract import HistoricalCaseConsumerService
from services.major_case_publisher import MajorCasePublisher


ROOT = Path(__file__).resolve().parents[1]


def _issue(repository, knowledge_id, itr, description, extra=None):
    repository.save_issue(
        knowledge_id=knowledge_id,
        business_issue_id=itr,
        raw_json={"问题编号": itr, "问题描述": description, **(extra or {})},
        normalized_snapshot={
            "ISSUE_FACT": {
                "business_issue_id": itr,
                "title": description,
                "description": description,
                "product": "PLC",
                "version": "V2.3",
                "scene": "掉电恢复",
            }
        },
        mapping_config_id="MAP-PLC-V1",
        mapping_config_version=1,
        standard_catalog_version_id="SFC-PLC-V1",
        source_file_sha256=f"sha-{knowledge_id}",
        sheet_name="issues",
        row_number=1,
    )


def _entry(major, case_id, event_id, kind, content, *, status="CONFIRMED", origin="HUMAN", evidence=()):
    return major.add_entry(
        case_id, kind, content, assertion_kind="FACT", origin=origin,
        status=status, event_id=event_id, evidence=evidence,
    )


def test_publish_to_itr_repeat_decision_and_refresh(tmp_path):
    """One published event must flow into the existing ITR workbench."""
    major = MajorKnowledgeRepository(tmp_path / "major.sqlite3", tmp_path / "attachments")
    artifacts = JsonArtifactRepository(tmp_path / "published")
    case = major.create_case("掉电恢复启动失败", "G1", domain="PLC")
    history = major.upsert_event(
        case["case_id"], standard_itr="ITR-H-1", internal_event_key="ITR-H-1",
        title="ITR-H-1",
    )
    other = major.upsert_event(
        case["case_id"], standard_itr="ITR-H-OTHER", internal_event_key="ITR-H-OTHER",
        title="ITR-H-OTHER",
    )
    major.update_case_status(case["case_id"], "ACTIVE")
    link = major.add_source_link(
        case["case_id"], history["event_id"],
        {"record_id": "ROW-99", "source_type": "ITR", "source_system": "BUSINESS_DB", "group_code": "G1"},
        standard_itr="ITR-H-1", role="CURRENT_EVENT", status="LINKED",
    )
    evidence = [{
        "source_link_id": link["source_link_id"], "locator": "root_cause",
        "excerpt": "原始记录：保存路径在掉电窗口存在未完成写入",
    }]
    _entry(major, case["case_id"], history["event_id"], "ISSUE_FACT", "掉电恢复后启动失败")
    _entry(major, case["case_id"], history["event_id"], "ROOT_CAUSE", "保存路径在掉电窗口存在未完成写入", evidence=evidence)
    _entry(major, case["case_id"], history["event_id"], "ACTION", "增加原子保存与恢复校验")
    _entry(major, case["case_id"], history["event_id"], "VERIFICATION", "100 次掉电恢复验证通过")
    _entry(major, case["case_id"], history["event_id"], "ROOT_CAUSE", "AI 猜测：电源故障", status="PENDING", origin="AI")
    _entry(major, case["case_id"], other["event_id"], "ROOT_CAUSE", "其他 Event 的根因")

    published = MajorCasePublisher(major, artifacts).publish_event(history["event_id"])
    assert published["publication_status"] == "PUBLISHED"
    case_id = published["case_id"]
    assert MajorCasePublisher(major, artifacts).publish_event(history["event_id"])["case_id"] == case_id

    calls = {"search": 0, "fail": False}

    def retrieval_adapter(query, top_k):
        calls["search"] += 1
        if calls["fail"]:
            raise RuntimeError("synthetic search outage")
        results = []
        for path in artifacts.list("knowledge/retrieval_docs"):
            doc = artifacts.load(path)
            if doc and "掉电恢复" in query.text and "掉电恢复" in doc["text"]:
                results.append({
                    "case_id": doc["case_id"], "title": doc["title"],
                    "summary": doc["title"], "score": 0.87, "rank": len(results) + 1,
                    "reasons": ["问题均发生于掉电恢复场景"],
                    "matched_fields": ["problem"],
                })
        return {"results": results[:top_k]}

    historical = HistoricalCaseConsumerService(artifacts, repeat_search=retrieval_adapter)
    detail = historical.get_case(case_id)
    assert detail["root_cause"] == "保存路径在掉电窗口存在未完成写入"
    assert detail["solution"] == "增加原子保存与恢复校验"
    assert detail["verification_result"] == "100 次掉电恢复验证通过"
    assert "AI 猜测" not in repr(detail)
    assert "其他 Event" not in repr(detail)
    assert detail["evidence"][0]["raw_text"].startswith("原始记录")

    p0_db = tmp_path / "p0.sqlite3"
    P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    ).initialize(p0_db)
    issues = P0Repository(p0_db)
    _issue(issues, "K-ITR-1", "ITR-1", "当前控制器掉电后启动失败", {"关联漏测问题": "MISS-1"})
    _issue(issues, "K-MISS-1", "MISS-1", "掉电恢复场景漏测", {"测试缺口": "未覆盖写入中的掉电"})
    _issue(issues, "K-ITR-2", "ITR-2", "当前控制器偶发重启")
    repeat = RepeatQueryTraceRepository(tmp_path / "repeat.sqlite3")
    facade = RepeatWebFacade(issue_repository=issues, repeat_repository=repeat, case_service=historical)
    client = TestClient(create_p0_app(p0_db, stage_runner=object(), repeat_web=facade))
    assert client.get("/p0/issues/K-ITR-1").status_code == 200
    listed = client.get("/api/v2/historical-cases").json()
    assert listed["status_filter"] == "PUBLISHED"
    assert [item["case_id"] for item in listed["items"]] == [case_id]

    response = client.post(
        "/api/v2/issues/K-ITR-1/repeat-risk/queries",
        json={"include_missed_test": True, "top_k": 5},
    )
    assert response.status_code == 201, response.text
    result = response.json()["result"]
    assert result["subject_ref"] == "ITR-1"
    assert result["query_snapshot"]["include_missed_test"] is True
    assert result["query_snapshot"]["missed_test_ref"] == "MISS-1"
    trace = repeat.get(result["query_id"])
    assert trace["subject_ref"] == "ITR-1"
    assert trace["optional_context"]["missed_test_ref"] == "MISS-1"
    assert result["result_status"] == "READY_FOR_REVIEW"
    candidate = result["candidates"][0]
    assert candidate["case_id"] == case_id
    assert candidate["why_relevant"][0]["text"] == "问题均发生于掉电恢复场景"
    assert candidate["root_causes"] == ["保存路径在掉电窗口存在未完成写入"]
    assert candidate["measures"] == ["增加原子保存与恢复校验"]
    assert candidate["evidence"][0]["raw_text"].startswith("原始记录")
    assert client.get(f"/api/v2/historical-cases/{case_id}").json()["evidence"] == detail["evidence"]
    saved = client.post(
        f"/api/v2/repeat-risk/queries/{result['query_id']}/decision",
        json={"decision": "SIMILAR", "decided_by": "golden-reviewer", "reason": "人工核对 Evidence"},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["human_decision"]["decision"] == "SIMILAR"

    before_refresh = calls["search"]
    restored = client.get("/api/v2/issues/K-ITR-1/repeat-risk/result")
    assert restored.status_code == 200
    assert restored.json()["result"]["query_id"] == result["query_id"]
    assert restored.json()["result"]["human_decision"]["decision"] == "SIMILAR"
    assert calls["search"] == before_refresh
    isolated = client.get("/api/v2/issues/K-ITR-2/repeat-risk/result")
    assert isolated.status_code == 200
    assert isolated.json() == {"state": "NOT_RUN", "result": None}

    calls["fail"] = True
    failed = client.post(
        "/api/v2/issues/K-ITR-1/repeat-risk/queries",
        json={"include_missed_test": False, "top_k": 5},
    )
    assert failed.status_code == 201
    failed_result = failed.json()["result"]
    assert failed_result["result_status"] == "SEARCH_UNAVAILABLE"
    assert failed_result["human_decision"]["decision"] == "PENDING"
    assert failed_result["query_snapshot"]["missed_test_ref"] is None
    assert repeat.get(failed_result["query_id"])["optional_context"] is None
    assert "NOT_REPEAT" not in failed_result["result_status"]
    assert major.db_path != p0_db != repeat.db_path


def test_repeat_domain_boundary_has_no_major_or_knowledge_repository_imports():
    for path in (ROOT / "quality_knowledge/repeat_risk").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "major_cases.repository" not in source
        assert "JsonArtifactRepository" not in source
        if path.name != "repository.py":
            assert "sqlite3.connect" not in source
    web = (ROOT / "quality_knowledge/web/repeat_risk_integration.py").read_text(encoding="utf-8")
    assert "major_cases.repository" not in web
    assert "knowledge.sqlite" not in web
