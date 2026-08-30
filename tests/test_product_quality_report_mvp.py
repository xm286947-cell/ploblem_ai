from pathlib import Path
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.p0.repository import P0Repository
from quality_knowledge.web.p0_app import create_p0_app
from quality_knowledge.web.app import create_app
from quality_knowledge.repositories import IssueKnowledgeRepository
from quality_knowledge.services.knowledge_issue_service import KnowledgeIssueService
from quality_knowledge.product_report.legacy_service import LegacyProductQualityReportService

ROOT = Path(__file__).parents[1]


def _client(tmp_path):
    db = tmp_path / "report.db"
    P0Initializer(manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json", plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml").initialize(db)
    repo = P0Repository(db)
    with repo.connect() as c:
        product_id = c.execute("SELECT product_id FROM product_config WHERE product_code='PLC'").fetchone()[0]
    issue = repo.save_issue(knowledge_id="K-REPORT-1", business_issue_id="B-REPORT-1", raw_json={"问题":"测试"},
        normalized_snapshot={"ISSUE_FACT":{"business_issue_id":"B-REPORT-1","product":"PLC","month":"8月","severity":"HIGH"}},
        mapping_config_id="MAP-PLC-V1", mapping_config_version=1, standard_catalog_version_id="SFC-PLC-V1",
        source_file_sha256="report", sheet_name="issues", row_number=1, product_id=product_id)
    repo.save_analysis_set({"analysis_set_id":"AS-REPORT-1","knowledge_id":"K-REPORT-1","issue_version_id":issue["issue_version_id"],
        "taxonomy_version_id":"QUALITY_TAXONOMY_P0_V1","input_hash":"report","status":"COMPLETED",
        "capability_gaps":[{"capability_axis":"QUALITY_ENGINEERING","capability_code":"TEST_VERIFICATION","governance_scope":"PRODUCT","control_status":"NOT_DEFINED","details":{"priority":"P0"},"confidence":.6}],
        "mrc":[{"side":"OCCURRENCE","mrc_code":"CHANGE_IMPACT_NOT_ASSESSED","role":"PRIMARY","confidence":.6}]})
    return TestClient(create_p0_app(db))


def test_report_page_precheck_create_publish_and_history(tmp_path):
    client = _client(tmp_path)
    assert client.get("/p1/product-reports").status_code == 200
    assert client.get("/p0/static/p1_product_reports.css").status_code == 200
    check = client.get("/api/v2/product-reports/precheck", params={"product_code":"PLC","start_month":"1月","end_month":"12月"}).json()
    assert check["issue_count"] == 1 and check["analysis_coverage_rate"] == 100.0
    created = client.post("/api/v2/product-reports", json={"product_code":"PLC","start_month":"1月","end_month":"12月"}).json()
    assert created["status"] == "REVIEW_REQUIRED"
    assert created["report"]["core_contradictions"][0]["name"]
    assert created["report"]["risk_summary"]["high_risk_count"] == 1
    published = client.post(f"/api/v2/product-reports/{created['report_id']}/publish").json()
    assert published["status"] == "PUBLISHED"
    assert client.get("/api/v2/product-reports").json()["total"] == 1


def test_report_ui_is_senior_quality_expert_and_not_dashboard_builder():
    html = (ROOT / "quality_knowledge/web/templates/p1_product_reports.html").read_text(encoding="utf-8")
    js = (ROOT / "quality_knowledge/web/static/p1_product_reports.js").read_text(encoding="utf-8")
    assert "质量工程能力差距" in js and "质量管理能力差距" in js
    assert "核心矛盾 TOP3" in js and "治理优先级" in js and "人工待确认" in js
    assert "拖拽" not in html and "自动发布" not in html
    for token in ("这段时间发生了哪些问题", "为什么发生", "测试为什么没有发现", "质量工程能力差距", "质量管理能力差距"):
        assert token in js
    prompt = (ROOT / "quality_knowledge/prompts/product_quality_report.md").read_text(encoding="utf-8")
    assert "单问题 AI 分析摘要" in prompt and "evidence_issue_ids" in prompt


def test_stable_knowledge_web_exposes_report_entry_and_assets(tmp_path):
    client = TestClient(create_app(tmp_path / "legacy-report.db"))
    page = client.get("/product-reports")
    assert page.status_code == 200 and "产品质量综合报告" in page.text
    assert '/static/p1_product_reports.css' in page.text
    assert '/static/p1_product_reports.js' in page.text
    assert "/product-reports" in client.get("/issues").text
    assert client.get("/static/p1_product_reports.css").status_code == 200
    assert client.get("/api/product-reports").status_code == 200
    products = client.get("/api/settings/products").json()["items"]
    assert products and all(x.get("product_code") for x in products)


def test_stable_knowledge_web_report_flow_uses_real_issue_scope(tmp_path):
    db = tmp_path / "legacy-flow.db"
    repo = IssueKnowledgeRepository(db)
    with repo.connect() as c:
        c.execute("INSERT INTO quality_issue(knowledge_id,business_type,business_issue_id,current_version_id) VALUES(?,?,?,?)",("K-LR-1","PLC","ITR-LR-1","K-LR-1-V1"))
        c.execute("INSERT INTO quality_issue_version(issue_version_id,knowledge_id,version_no,normalized_source_hash,title,month,severity,normalized_json) VALUES(?,?,?,?,?,?,?,?)",("K-LR-1-V1","K-LR-1",1,"legacy-report","报告验证问题","8月","H","{}"))
    client = TestClient(create_app(db))
    check = client.get("/api/product-reports/precheck", params={"product_code":"PLC","start_month":"1月","end_month":"12月"})
    assert check.status_code == 200 and check.json()["issue_count"] == 1
    assert check.json()["ready"] is False
    created = client.post("/api/product-reports", json={"product_code":"PLC","start_month":"1月","end_month":"12月"})
    assert created.status_code == 400
    assert created.json()["detail"] == "PRODUCT_REPORT_AI_SUMMARY_REQUIRED"


def test_legacy_report_synthesizes_issue_ai_summaries_and_keeps_real_evidence(tmp_path, monkeypatch):
    repo = IssueKnowledgeRepository(tmp_path / "synthesis.db")
    issue_service = KnowledgeIssueService(repo)
    def complete(messages):
        payload=json.loads(messages[-1]["content"])
        if payload["mode"]=="EXECUTIVE_FINAL":body={"executive_summary":"PLC 在 8 月集中暴露变更影响评估不足。","core_contradictions":[{"title":"变更速度与验证矛盾","evidence_issue_ids":["K-1"]}],"manual_confirmation_questions":[]}
        else:body={"dimension":payload["dimension"],"items":[{"title":"变更影响遗漏","judgement":"共同问题","evidence_issue_ids":["K-1","FAKE"]}]}
        return SimpleNamespace(content=json.dumps(body,ensure_ascii=False),model="fake-model")
    fake = SimpleNamespace(complete=complete)
    service = LegacyProductQualityReportService(issue_service, ROOT, fake)
    def latest(kid,stage):
        results={"occurrence":{"root_cause_summary":{"value":"变更影响遗漏"}},"escape":{"escape_cause_summary":{"value":"回归不足"}},"recurrence":{"customer_impact":"功能不可用"},"capability_gap":{"capability_gaps":[{"dimension":"TECHNICAL","category":"TEST"},{"dimension":"MANAGEMENT","category":"GATE"}]}}
        return {"result":results[stage]}
    monkeypatch.setattr(repo, "get_latest_analysis", latest)
    result = service._ai_synthesis("PLC", "8月", "8月", [{"knowledge_id":"K-1","business_issue_id":"B-1","title":"变更后异常","month":"8月","severity":"H"}])
    assert result["synthesis_status"] == "COMPLETED"
    assert result["synthesis_agent"] == "DEFAULT"
    assert result["synthesis_model"] == "fake-model"
    assert result["product_ai_synthesis"]["problem_landscape"][0]["evidence_issue_ids"] == ["K-1"]
    assert "变更影响" in result["overall_judgement"]


def test_large_report_uses_map_reduce_one_model_and_reuses_batch_cache(tmp_path, monkeypatch):
    repo = IssueKnowledgeRepository(tmp_path / "large-synthesis.db")
    issue_service = KnowledgeIssueService(repo)
    calls=[]
    def complete(messages):
        payload=json.loads(messages[-1]["content"]);calls.append(payload["mode"])
        if payload["mode"] == "EXECUTIVE_FINAL":
            ids=[x for d in payload["dimensions"] for item in d["items"] for x in item["evidence_issue_ids"]]
            body={"executive_summary":"大量问题分维度综合完成","core_contradictions":[{"title":"交付与验证矛盾","evidence_issue_ids":ids[:20]}],"manual_confirmation_questions":[]}
        elif payload["mode"] == "DIMENSION_REDUCE":
            ids=[x for b in payload["batch_results"] for item in b["items"] for x in item["evidence_issue_ids"]]
            body={"dimension":payload["dimension"],"items":[{"title":"维度结论","judgement":"共同问题","evidence_issue_ids":ids[:20]}]}
        else:
            ids=[x["knowledge_id"] for x in payload["records"]]
            body={"dimension":payload["dimension"],"items":[{"title":"维度结论","judgement":"共同问题","evidence_issue_ids":ids[:20]}]}
        return SimpleNamespace(content=json.dumps(body,ensure_ascii=False),model="one-model")
    service=LegacyProductQualityReportService(issue_service,ROOT,SimpleNamespace(complete=complete))
    def latest(kid,stage):
        results={"occurrence":{"root_cause_summary":{"value":"共同原因"}},"escape":{"escape_cause_summary":{"value":"漏测原因"}},"recurrence":{"customer_impact":"客户影响"},"capability_gap":{"capability_gaps":[{"dimension":"TECHNICAL","category":"TEST"},{"dimension":"MANAGEMENT","category":"GATE"}]}}
        return {"result":results[stage]}
    monkeypatch.setattr(repo,"get_latest_analysis",latest)
    rows=[{"knowledge_id":f"K-{i}","business_issue_id":f"B-{i}","title":f"问题{i}","month":"8月","severity":"M"} for i in range(61)]
    first=service._ai_synthesis("PLC","8月","8月",rows)
    assert first["synthesis_strategy"] == "DIMENSIONAL_MAP_REDUCE"
    assert first["synthesis_dimension_count"] == 5 and first["synthesis_batch_count"] == 20 and first["synthesis_model"] == "one-model"
    assert calls.count("DIMENSION_MAP")==20 and calls.count("DIMENSION_REDUCE")==5 and calls[-1]=="EXECUTIVE_FINAL"
    second=service._ai_synthesis("PLC","8月","8月",rows)
    assert second["synthesis_cache_hits"] == 20
    assert calls[-1] == "EXECUTIVE_FINAL" and len(calls) == 32


def test_stable_web_deletes_only_unpublished_reports(tmp_path):
    db=tmp_path/"delete-report.db";client=TestClient(create_app(db));repo=IssueKnowledgeRepository(db)
    with repo.connect() as c:
        for rid,status in (("DRAFT-1","REVIEW_REQUIRED"),("PUB-1","PUBLISHED")):
            c.execute("INSERT INTO product_quality_report(report_id,product_code,start_month,end_month,status) VALUES(?,?,?,?,?)",(rid,"PLC","1月","2月",status))
            c.execute("INSERT INTO product_quality_report_version(report_version_id,report_id,version_no,status,scope_hash,report_json) VALUES(?,?,?,?,?,?)",(rid+"-V1",rid,1,status,"scope",json.dumps({"scope":{}},ensure_ascii=False)))
        c.commit()
    deleted=client.delete("/api/product-reports/DRAFT-1")
    assert deleted.status_code==200 and deleted.json()["deleted"] is True
    assert client.get("/api/product-reports/DRAFT-1").status_code==404
    blocked=client.delete("/api/product-reports/PUB-1")
    assert blocked.status_code==409 and blocked.json()["detail"]=="PUBLISHED_REPORT_CANNOT_BE_DELETED"


def test_report_page_has_draft_delete_control():
    html=(ROOT/"quality_knowledge/web/templates/p1_product_reports.html").read_text(encoding="utf-8")
    script=(ROOT/"quality_knowledge/web/static/p1_product_reports_delete.js").read_text(encoding="utf-8")
    assert "p1_product_reports_delete.js" in html and "删除草稿" in script and "method:'DELETE'" in script


def test_report_retries_compact_json_and_saves_full_raw_diagnostics(tmp_path):
    repo=IssueKnowledgeRepository(tmp_path/"retry.db");service=LegacyProductQualityReportService(KnowledgeIssueService(repo),tmp_path)
    valid={"executive_summary":"成功","problem_landscape":[{"theme":"问题","evidence_issue_ids":["K-1"]}],"occurrence_diagnosis":[{"cause":"原因","evidence_issue_ids":["K-1"]}],"escape_diagnosis":[{"escape_reason":"漏测","evidence_issue_ids":["K-1"]}],"engineering_capability_gaps":[{"gap":"工程","evidence_issue_ids":["K-1"]}],"management_capability_gaps":[{"gap":"管理","evidence_issue_ids":["K-1"]}],"core_contradictions":[{"title":"矛盾","evidence_issue_ids":["K-1"]}]}
    responses=iter([SimpleNamespace(content='{"executive_summary":"broken"',model='default',raw={"choices":[{"finish_reason":None}],"usage":{"total_tokens":39305}}),SimpleNamespace(content=json.dumps(valid,ensure_ascii=False),model='default',raw={"choices":[{"finish_reason":"stop"}]})])
    parsed,model=service._complete(SimpleNamespace(complete=lambda messages:next(responses)),"prompt",{"mode":"DIRECT"},{"K-1"})
    assert parsed["executive_summary"]=="成功" and model=="default"
    metas=sorted((tmp_path/"output/raw_ai/product_report").glob("*.meta.json"))
    assert len(metas)==2
    first=json.loads(metas[0].read_text(encoding="utf-8"))
    assert first["usage"]["total_tokens"]==39305 and first["output_chars"]>0
