from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.p0.repository import P0Repository
from quality_knowledge.repeat_risk import RepeatQueryTraceRepository
from quality_knowledge.web.p0_app import create_p0_app
from quality_knowledge.web.repeat_risk_integration import RepeatWebFacade
from repositories import JsonArtifactRepository
from services.historical_case_contract import HistoricalCaseConsumerService


ROOT = Path(__file__).parents[1]
WEB = ROOT / "quality_knowledge" / "web"


def _init(db_path: Path) -> None:
    P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    ).initialize(db_path)


def _save_issue(
    repository: P0Repository,
    *,
    knowledge_id: str,
    business_id: str,
    title: str,
    raw_extra: dict | None = None,
) -> None:
    raw = {"问题编号": business_id, "问题描述": title}
    raw.update(raw_extra or {})
    repository.save_issue(
        knowledge_id=knowledge_id,
        business_issue_id=business_id,
        raw_json=raw,
        normalized_snapshot={
            "ISSUE_FACT": {
                "business_issue_id": business_id,
                "title": title,
                "description": title,
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


def _seed_case_artifacts(repo: JsonArtifactRepository) -> None:
    repo.save(
        "knowledge/enriched_case/HCASE-1.json",
        {
            "metadata": {"case_id": "HCASE-1", "knowledge_revision": "REV-1"},
            "business_context": {"product": "PLC"},
            "problem": {
                "standard_description": "历史控制器掉电恢复后启动失败",
                "phenomenon": [{"value": "掉电恢复后启动失败"}],
            },
            "analysis": {"root_cause": [{"value": "保存路径在掉电窗口存在未完成写入"}]},
            "solution": {
                "corrective_actions": [{"value": "增加原子保存与恢复校验"}],
                "preventive_actions": [],
                "reusable_actions": [],
                "verification_result": "100 次掉电恢复验证通过",
            },
            "knowledge": {"case_summary": "掉电恢复启动失败"},
            "status": "ACTIVE",
        },
    )
    repo.save(
        "knowledge/raw_evidence/HCASE-1.json",
        {
            "case_id": "HCASE-1",
            "source_type": "REPORT",
            "source_id": "ITR-H-1",
            "sections": [
                {
                    "source_type": "REPORT",
                    "source_id": "ITR-H-1",
                    "file_name": "ITR-H-1.pdf",
                    "page": 7,
                    "page_numbers": [7],
                    "section": "Root Cause",
                    "raw_text": "掉电窗口存在未完成写入。",
                    "url": "https://example.invalid/source",
                }
            ],
        },
    )
    repo.save(
        "knowledge/retrieval_docs/HCASE-1.json",
        {
            "case_id": "HCASE-1",
            "title": "掉电恢复启动失败",
            "text": "掉电恢复 启动失败 保存路径 未完成写入",
            "content_hash": "hash-1",
            "filters": {"knowledge_source": "MAJOR_EVENT"},
        },
    )
    repo.save(
        "knowledge/publication_metadata/major_event/published.json",
        {
            "publication_status": "PUBLISHED",
            "case_id": "HCASE-1",
            "business_id": "ITR-H-1",
            "knowledge_revision": "REV-1",
            "published_at": "2026-09-24T08:00:00+08:00",
        },
    )
    repo.save(
        "knowledge/publication_metadata/major_event/draft.json",
        {
            "publication_status": "DRAFT",
            "case_id": "HCASE-DRAFT",
            "business_id": "ITR-H-DRAFT",
            "knowledge_revision": "REV-DRAFT",
        },
    )


def _client(tmp_path: Path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = tmp_path / "p0.db"
    _init(db)
    p0 = P0Repository(db)
    _save_issue(
        p0,
        knowledge_id="K-ITR-1",
        business_id="ITR-1",
        title="当前控制器掉电后启动失败",
        raw_extra={"关联漏测问题": "MISS-1"},
    )
    _save_issue(
        p0,
        knowledge_id="K-MISS-1",
        business_id="MISS-1",
        title="掉电恢复场景漏测",
        raw_extra={"测试缺口": "未覆盖写入中的掉电"},
    )
    _save_issue(
        p0,
        knowledge_id="K-ITR-2",
        business_id="ITR-2",
        title="当前控制器偶发重启",
    )

    artifacts = JsonArtifactRepository(tmp_path / "artifacts")
    _seed_case_artifacts(artifacts)
    mode = {"state": "success", "calls": 0}

    def repeat_search(query, top_k):
        mode["calls"] += 1
        if mode["state"] == "unavailable":
            raise RuntimeError("synthetic search outage")
        if mode["state"] == "empty":
            return {"results": []}
        case_id = "HCASE-MISSING" if mode["state"] == "incomplete" else "HCASE-1"
        return {
            "results": [
                {
                    "case_id": case_id,
                    "title": "掉电恢复启动失败",
                    "summary": "历史掉电恢复案例",
                    "score": 0.87,
                    "rank": 1,
                    "reasons": [
                        "问题均发生于掉电恢复场景",
                        "当前现象与历史启动失败特征一致",
                    ],
                    "matched_fields": ["problem", "cause"],
                }
            ][: top_k or 1]
        }

    case_service = HistoricalCaseConsumerService(
        artifacts,
        repeat_search=repeat_search,
    )
    facade = RepeatWebFacade(
        issue_repository=p0,
        repeat_repository=RepeatQueryTraceRepository(tmp_path / "repeat.db"),
        case_service=case_service,
    )
    app = create_p0_app(db, stage_runner=None, repeat_web=facade)
    return TestClient(app), mode


def _query(client: TestClient, knowledge_id: str, include: bool = False):
    return client.post(
        f"/api/v2/issues/{knowledge_id}/repeat-risk/queries",
        json={"include_missed_test": include, "top_k": 5},
    )


def test_h01_h10_pages_are_in_existing_p0_product(tmp_path: Path):
    client, _ = _client(tmp_path)
    workbench = client.get("/p0/issues/K-ITR-1")
    case_list = client.get("/p0/cases")
    case_detail = client.get("/p0/cases/HCASE-1")
    assert workbench.status_code == case_list.status_code == case_detail.status_code == 200
    for text in ("Repeat Risk", "查询历史类似问题", "Evidence"):
        assert text in workbench.text
    assert "重大问题案例库" in case_list.text
    assert "重大问题案例详情" in case_detail.text


def test_itr_with_missed_test_selected_success(tmp_path: Path):
    client, _ = _client(tmp_path)
    state = client.get("/api/v2/issues/K-ITR-1/repeat-risk").json()
    assert state["subject"]["source"] == "ITR_RESOLUTION_WORKBENCH"
    assert state["subject"]["itr_ref"] == "ITR-1"
    assert state["optional_context"]["missed_test_ref"] == "MISS-1"

    response = _query(client, "K-ITR-1", include=True)
    assert response.status_code == 201
    result = response.json()["result"]
    assert result["result_status"] == "READY_FOR_REVIEW"
    assert result["query_snapshot"]["include_missed_test"] is True
    assert result["query_snapshot"]["missed_test_ref"] == "MISS-1"


def test_itr_with_missed_test_not_selected_success(tmp_path: Path):
    client, _ = _client(tmp_path)
    result = _query(client, "K-ITR-1", include=False).json()["result"]
    assert result["result_status"] == "READY_FOR_REVIEW"
    assert result["query_snapshot"]["include_missed_test"] is False
    assert result["query_snapshot"]["missed_test_ref"] is None


def test_itr_without_missed_test_success(tmp_path: Path):
    client, _ = _client(tmp_path)
    state = client.get("/api/v2/issues/K-ITR-2/repeat-risk").json()
    assert state["optional_context"]["missed_test_available"] is False
    assert _query(client, "K-ITR-2").json()["result"]["result_status"] == "READY_FOR_REVIEW"


def test_success_candidate_evidence_roundtrip(tmp_path: Path):
    client, _ = _client(tmp_path)
    result = _query(client, "K-ITR-1").json()["result"]
    candidate = result["candidates"][0]
    assert candidate["why_relevant"][0]["text"] == "问题均发生于掉电恢复场景"
    assert candidate["historical_phenomenon"] == "掉电恢复后启动失败"
    assert candidate["root_causes"] == ["保存路径在掉电窗口存在未完成写入"]
    assert candidate["measures"] == ["增加原子保存与恢复校验"]
    assert candidate["evidence"][0]["raw_text"] == "掉电窗口存在未完成写入。"
    detail = client.get("/api/v2/historical-cases/HCASE-1").json()
    assert detail["evidence"][0]["page"] == 7


def test_all_four_human_decisions_persist(tmp_path: Path):
    for decision in ("REPEAT", "SIMILAR", "NOT_REPEAT", "INSUFFICIENT_EVIDENCE"):
        client, _ = _client(tmp_path / decision.lower())
        result = _query(client, "K-ITR-1").json()["result"]
        saved = client.post(
            f"/api/v2/repeat-risk/queries/{result['query_id']}/decision",
            json={"decision": decision, "decided_by": "tester", "reason": "人工核对 Evidence"},
        )
        assert saved.status_code == 200
        assert saved.json()["human_decision"]["decision"] == decision
        assert saved.json()["human_decision"]["decided_by"] == "tester"


def test_empty_does_not_generate_not_repeat(tmp_path: Path):
    client, mode = _client(tmp_path)
    mode["state"] = "empty"
    result = _query(client, "K-ITR-1").json()["result"]
    assert result["result_status"] == "NO_CANDIDATES"
    assert result["human_decision"]["decision"] == "PENDING"
    assert "NOT_REPEAT" not in result["result_status"]


def test_search_unavailable_is_retryable_state(tmp_path: Path):
    client, mode = _client(tmp_path)
    mode["state"] = "unavailable"
    result = _query(client, "K-ITR-1").json()["result"]
    assert result["result_status"] == "SEARCH_UNAVAILABLE"
    assert result["human_decision"]["decision"] == "PENDING"
    js = (WEB / "static/p0_issue_detail.js").read_text(encoding="utf-8")
    assert "data-repeat-retry" in (WEB / "templates/p0_issue_detail.html").read_text(encoding="utf-8")
    assert "runRepeatQuery" in js


def test_incomplete_keeps_existing_candidate(tmp_path: Path):
    client, mode = _client(tmp_path)
    mode["state"] = "incomplete"
    result = _query(client, "K-ITR-1").json()["result"]
    assert result["result_status"] == "INCOMPLETE"
    assert result["candidate_count"] == 1
    assert result["candidates"][0]["case_id"] == "HCASE-MISSING"
    assert result["candidates"][0]["detail_status"] == "INCOMPLETE"


def test_evidence_missing_has_explicit_web_message():
    detail_js = (WEB / "static/p0_case_detail.js").read_text(encoding="utf-8")
    issue_js = (WEB / "static/p0_issue_detail.js").read_text(encoding="utf-8")
    message = "当前知识存在，但没有可用原始 Evidence。"
    assert message in detail_js
    assert message in issue_js


def test_refresh_restores_existing_query_without_rerun(tmp_path: Path):
    client, mode = _client(tmp_path)
    first = _query(client, "K-ITR-1").json()["result"]
    calls_after_query = mode["calls"]
    restored = client.get("/api/v2/issues/K-ITR-1/repeat-risk/result")
    assert restored.status_code == 200
    assert restored.json()["result"]["query_id"] == first["query_id"]
    assert mode["calls"] == calls_after_query


def test_case_list_defaults_to_published_only(tmp_path: Path):
    client, _ = _client(tmp_path)
    data = client.get("/api/v2/historical-cases").json()
    assert data["status_filter"] == "PUBLISHED"
    assert data["total"] == 1
    assert data["items"][0]["case_id"] == "HCASE-1"
    assert data["items"][0]["status"] == "PUBLISHED"


def test_case_detail_evidence_and_unconfirmed_fields_are_not_ai_filled(tmp_path: Path):
    client, _ = _client(tmp_path)
    detail = client.get("/api/v2/historical-cases/HCASE-1").json()
    assert detail["root_cause"] == "保存路径在掉电窗口存在未完成写入"
    assert detail["solution"] == "增加原子保存与恢复校验"
    assert detail["evidence"][0]["raw_text"] == "掉电窗口存在未完成写入。"
    js = (WEB / "static/p0_case_detail.js").read_text(encoding="utf-8")
    assert "未确认 / 无已确认内容" in js
    assert "不自行分类" in js


def test_static_contract_rationale_first_similarity_secondary_and_no_new_repeat_app():
    html = (WEB / "templates/p0_issue_detail.html").read_text(encoding="utf-8")
    js = (WEB / "static/p0_issue_detail.js").read_text(encoding="utf-8")
    pages = (WEB / "p0_pages.py").read_text(encoding="utf-8")
    assert html.index("data-repeat-risk") < html.index('id="gaps"')
    assert js.index("为什么值得关注") < js.index("Similarity")
    assert "查询主体" in js and "当前 ITR" in js
    assert "Optional Context" in js
    assert "data-repeat-evidence-drawer" in html
    assert "/p0/repeat" not in pages
    assert "Repeat App" not in pages
