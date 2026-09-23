import json
import threading
import time

from fastapi.testclient import TestClient

from quality_knowledge.repositories import IssueKnowledgeRepository
from quality_knowledge.web.app import create_app
from quality_knowledge.services.v1_analysis_service import KnowledgeIssueAnalysisService
from quality_knowledge.batch_analysis_jobs import BatchAnalysisJobManager
from quality_knowledge.mapping.migration import LegacyYamlMappingMigrator
from quality_knowledge.mapping.models import MappingItem


def _seed(db):
    repository = IssueKnowledgeRepository(db)
    normalized = {
        "issue_fact": {"business_issue_id": "LEGACY-1", "title": "版本变更后功能异常"},
        "occurrence": {"cause_l1": "变更"}, "escape": {"escape_l1": "发布门禁"},
    }
    with repository.connect() as connection:
        connection.execute("INSERT INTO quality_issue(knowledge_id,business_type,business_issue_id,current_version_id) VALUES('K-LEGACY-1','PLC','LEGACY-1','K-LEGACY-1-V1')")
        connection.execute(
            """INSERT INTO quality_issue_version(
                 issue_version_id,knowledge_id,version_no,normalized_source_hash,title,description,
                 product,platform,issue_domain,issue_domain_source,normalized_json)
               VALUES('K-LEGACY-1-V1','K-LEGACY-1',1,'hash','版本变更后功能异常','版本合入后功能异常',
                      'PLC','PLC','SOFTWARE','USER',?)""", (json.dumps(normalized),),
        )
        connection.execute("INSERT INTO issue_source_raw_v1(issue_version_id,raw_json,source_hash) VALUES('K-LEGACY-1-V1','{}','raw')")
        for run_id, stage, result in (
            ("RUN-O", "occurrence", {"occurrence_category": "CHANGE_IMPACT", "introduced_phase": "IMPLEMENTATION", "lifecycle_tags":[{"code":"DESIGN DESIGN","source_type":"AI_STANDARDIZED","confidence":.8},{"code":"PID AT模块单元测试","source_type":"AI_INFERRED","confidence":.7}],"issue_type_tags":[{"code":"FUNCTIONAL FUNCTIONAL","source_type":"AI_INFERRED","confidence":.7}],"root_cause_summary": {"value": "变更影响未完整评估", "confidence": .88}}),
            ("RUN-E", "escape", {"escape_category": "RELEASE_GATE", "actual_detection_stage": "DELIVERY", "escape_cause_summary": {"value": "发布门禁未阻断", "confidence": .82}}),
        ):
            connection.execute("INSERT INTO analysis_run(analysis_run_id,knowledge_id,issue_version_id,analysis_type,status) VALUES(?,?,?,?, 'COMPLETED')", (run_id, "K-LEGACY-1", "K-LEGACY-1-V1", stage))
            connection.execute("INSERT INTO issue_ai_analysis(analysis_run_id,knowledge_id,issue_version_id,analysis_type,result_json) VALUES(?,?,?,?,?)", (run_id, "K-LEGACY-1", "K-LEGACY-1-V1", stage, json.dumps(result)))
        connection.execute(
            """INSERT INTO issue_capability_gap(
                 gap_id,analysis_run_id,knowledge_id,issue_version_id,dimension,category,description,confidence)
               VALUES('G-1','RUN-O','K-LEGACY-1','K-LEGACY-1-V1','MANAGEMENT','CHANGE_MANAGEMENT','变更管理缺口',.9)"""
        )
        connection.commit()


def test_legacy_detail_projects_quality_capability_without_replacing_old_tables(tmp_path):
    db = tmp_path / "legacy.db"
    _seed(db)
    client = TestClient(create_app(db))
    page = client.get("/issues/K-LEGACY-1")
    assert page.status_code == 200
    assert "质量能力标准化结论" in page.text
    assert "CHANGE_IMPACT" in page.text and "RELEASE_GATE" in page.text
    assert "变更影响评估不足" in page.text and "发布门禁未拦截" in page.text
    assert "问题领域" in page.text and "软件" in page.text
    assert "主要生命周期" in page.text and "实现阶段" in page.text
    assert "变更影响未完整评估" in page.text
    assert "问题领域与生命周期" in page.text and "功能缺陷" in page.text and "方案设计" in page.text
    assert "待确认标签" in page.text and "PID AT模块单元测试" in page.text
    assert "AI 标准化" in page.text and "AI 推断" in page.text
    assert "管理能力缺口" in page.text and "变更管理缺口" in page.text
    with IssueKnowledgeRepository(db).connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM qc_analysis_projection").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM quality_issue").fetchone()[0] == 1


def test_analysis_agent_diagnostic_exposes_effective_stage_routes(tmp_path):
    response=TestClient(create_app(tmp_path/'agents.db')).get('/api/analysis-agents')
    assert response.status_code==200
    data=response.json()
    assert data['assignment_strategy']=='ROUND_ROBIN_BY_ISSUE'
    assert isinstance(data['eligible_agent_ids'],list)


def test_legacy_statistics_contains_new_matrices_and_old_drilldown(tmp_path):
    db = tmp_path / "legacy.db"
    _seed(db)
    client = TestClient(create_app(db))
    client.get("/issues/K-LEGACY-1")  # creates the deterministic projection
    page = client.get("/statistics?business_type=PLC")
    assert page.status_code == 200
    assert "MRC × 质量能力" in page.text
    assert "生命周期 × 质量能力" in page.text
    assert "CHANGE_MANAGEMENT" in page.text
    assert "/issues?" in page.text
    assert "matrix_axis=MRC" in page.text
    assert "分析口径与数据覆盖" in page.text
    assert "原始 L1–L4 与当前分类对照" in page.text
    assert "变更" in page.text and "CHANGE_IMPACT" in page.text
    assert 'name="month"' in page.text and 'name="issue_domain"' in page.text and 'name="lifecycle"' in page.text
    lifecycle_page=client.get("/statistics?business_type=PLC&lifecycle=IMPLEMENTATION")
    assert lifecycle_page.status_code == 200
    assert '<option value="IMPLEMENTATION" selected>' in lifecycle_page.text
    empty_scope=client.get("/statistics?business_type=PLC&lifecycle=REQUIREMENT")
    assert empty_scope.status_code == 200
    assert "当前筛选范围" in empty_scope.text
    drill = client.get(
        "/issues?matrix_axis=MRC&matrix_code=CHANGE_IMPACT&gap_dimension=MANAGEMENT&gap_category=CHANGE_MANAGEMENT"
    )
    assert drill.status_code == 200
    assert "LEGACY-1" in drill.text


def test_human_mrc_revision_becomes_effective_and_updates_insight(tmp_path):
    db = tmp_path / "legacy.db"
    _seed(db)
    client = TestClient(create_app(db))
    client.get("/issues/K-LEGACY-1")
    saved = client.post("/issues/K-LEGACY-1/quality-confirmations", data={
        "occurrence_mrc": "REQUIREMENT_BASELINE_MISSING",
        "escape_mrc": "RELEASE_BRANCH_NOT_MERGED",
        "reason": "评审确认", "evidence": "变更单与发布记录", "confirmed_by": "tester",
    }, follow_redirects=False)
    assert saved.status_code == 303
    detail = client.get("/issues/K-LEGACY-1")
    assert "REQUIREMENT_BASELINE_MISSING" in detail.text
    assert "人工确认" in detail.text
    insights = client.get("/statistics?business_type=PLC")
    assert "REQUIREMENT_BASELINE_MISSING" in insights.text
    with IssueKnowledgeRepository(db).connect() as connection:
        row = connection.execute("SELECT reason,evidence,confirmed_by FROM qc_human_revision ORDER BY rowid DESC LIMIT 1").fetchone()
        assert tuple(row) == ("评审确认", "变更单与发布记录", "tester")


def test_software_issue_can_record_human_confirmed_hardware_failure(tmp_path):
    db = tmp_path / "legacy.db"
    _seed(db)
    client = TestClient(create_app(db))
    saved = client.post("/issues/K-LEGACY-1/hardware-components", data={
        "relevance": "CONFIRMED",
        "component_category": "STORAGE",
        "component_name": "eMMC",
        "manufacturer": "Example Vendor",
        "model_part_number": "EMMC-01",
        "board_module": "主控板",
        "reference_designator": "U12",
        "failure_mode": "写入异常",
        "failure_mechanism": "写放大导致寿命下降",
        "failure_cause": "日志频繁落盘",
        "detection_method": "SMART 寿命检查",
        "disposition": "限制写入并更换器件",
        "evidence": "现场寿命计数与日志",
        "confirmed_by": "quality-user",
    }, follow_redirects=False)
    assert saved.status_code == 303
    page = client.get("/issues/K-LEGACY-1")
    assert page.status_code == 200
    assert "硬件关联与器件失效" in page.text
    assert "eMMC" in page.text and "EMMC-01" in page.text
    assert "写放大导致寿命下降" in page.text
    assert "HUMAN_CONFIRMED" in page.text
    insights = client.get("/statistics?business_type=PLC")
    assert "软件问题中的硬件关联" in insights.text
    assert "STORAGE" in insights.text and "日志频繁落盘" in insights.text
    with IssueKnowledgeRepository(db).connect() as connection:
        component = connection.execute(
            "SELECT component_name,model_part_number,source_type FROM qc_issue_hardware_component"
        ).fetchone()
        failure = connection.execute(
            "SELECT failure_mode,failure_cause,source_type FROM qc_hardware_failure_analysis"
        ).fetchone()
        assert tuple(component) == ("eMMC", "EMMC-01", "HUMAN_CONFIRMED")
        assert tuple(failure) == ("写入异常", "日志频繁落盘", "HUMAN_CONFIRMED")


def test_legacy_batch_analysis_supports_two_workers():
    service = object.__new__(KnowledgeIssueAnalysisService)
    lock = threading.Lock()
    running = 0
    peak = 0

    def analyze(knowledge_id, **_kwargs):
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(.03)
        with lock:
            running -= 1
        return {"knowledge_id": knowledge_id, "status": "COMPLETED"}

    service.run_issue_analysis = analyze
    result = service.run_batch_analysis(["K1", "K2", "K3"], concurrency=2)
    assert result["concurrency"] == 2
    assert result["completed"] == 3
    assert peak == 2


def test_legacy_batch_job_exposes_progress_and_existing_page(tmp_path):
    job_db = tmp_path / "jobs.db"
    manager = BatchAnalysisJobManager(job_db)
    def runner(ids, progress_callback, **_kwargs):
        for kid in ids:
            progress_callback({"knowledge_id": kid, "status": "COMPLETED"})
        return {"total": len(ids), "completed": len(ids), "failed": 0, "items": []}
    job = manager.start(["K1", "K2"], runner, concurrency=2)
    for _ in range(50):
        current = manager.get(job["job_id"])
        if current["status"] not in {"QUEUED", "RUNNING"}:
            break
        time.sleep(.01)
    assert current["status"] == "COMPLETED"
    assert current["processed"] == 2
    restored = BatchAnalysisJobManager(job_db).get(job["job_id"])
    assert restored["status"] == "COMPLETED"
    assert restored["processed"] == 2

    db = tmp_path / "empty.db"
    page = TestClient(create_app(db)).get("/analysis?job_id=BAJ-DEMO")
    assert "并发数" in page.text and "data-batch-job" in page.text
    assert "/api/analysis-batch-jobs/" in page.text


def test_batch_job_keeps_failure_stage_duration_and_retry_control(tmp_path):
    db = tmp_path / "jobs.db"
    manager = BatchAnalysisJobManager(db)
    def runner(ids, progress_callback, **_kwargs):
        progress_callback({"knowledge_id": ids[0], "status": "FAILED", "failed_stage": "escape", "error": "timeout", "duration_ms": 1234})
        return {"total": 1, "completed": 0, "failed": 1, "items": []}
    job = manager.start(["K-FAILED"], runner, concurrency=2)
    for _ in range(50):
        current = manager.get(job["job_id"])
        if current["status"] not in {"QUEUED", "RUNNING"}: break
        time.sleep(.01)
    item=current["items"][0]
    assert (item["failed_stage"],item["duration_ms"],item["error"]) == ("escape",1234,"timeout")
    page=TestClient(create_app(db)).get("/analysis?job_id="+job["job_id"])
    assert page.status_code == 200
    assert "只重试失败项" in page.text
    assert "escape" in page.text and "1234 ms" in page.text and "timeout" in page.text


def test_mapping_validation_does_not_duplicate_same_source_header_and_alias(tmp_path):
    migrator=object.__new__(LegacyYamlMappingMigrator)
    first=MappingItem(mapping_id='M1',canonical_field='impact',source_headers=['变更影响'],aliases=['变更影响'],target_domain='ISSUE_FACT',target_field='impact')
    second=MappingItem(mapping_id='M2',canonical_field='change_impact',source_headers=['变更影响'],aliases=['变更影响'],target_domain='PRODUCT_EXTENSION',target_field='change_impact')
    key=MappingItem(mapping_id='M0',canonical_field='business_issue_id',source_headers=['ITR单号'],aliases=['ITR单号'],target_domain='ISSUE_FACT',target_field='business_issue_id',required=True)
    results=migrator.validate_items('PLC',[key,first,second])
    ambiguities=[x for x in results if x['code']=='ALIAS_AMBIGUITY']
    assert len(ambiguities)==1
    assert "impact and change_impact" in ambiguities[0]['message']
