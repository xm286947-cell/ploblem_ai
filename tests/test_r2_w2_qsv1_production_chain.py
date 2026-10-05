import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
import pytest

from quality_knowledge.web.p0_pages import create_p0_insights_router
from quality_knowledge.web.quality_scenario_v1_api import create_quality_scenario_v1_router
from quality_knowledge.quality_scenario_candidate_v1_service import CandidateV1Service
from quality_knowledge.quality_scenario_v1_store import SQLiteQualityScenarioV1Repository


PREFIX = "/api/v2/quality-scenario-workflow/v1"


def reverse_result(*, revision="BUNDLE-R1", run="RQRUN-R2-W2-1"):
    return {
        "result_version": "reverse-quality-v0.1",
        "analysis_id": "RQA-R2-W2-1",
        "run_id": run,
        "run_seq": 1,
        "identity": {
            "canonical_itr": "ITR-R2-W2-001",
            "product_code": "PLC",
            "taxonomy_version_id": "STV-R2-W2-1",
            "bundle_id": "SSB-R2-001",
            "bundle_revision": revision,
            "source_snapshot_id": "SNAP-R2-001",
        },
        "bundle_provenance": {
            "bundle_id": "SSB-R2-001",
            "bundle_revision": revision,
            "snapshot_id": "SNAP-R2-001",
            "selected_issue": {
                "business_issue_id": "ITR-R2-W2-001",
                "software_assessment_record_id": "ASSESS-R2-001",
                "product_code": "PLC",
            },
            "source_refs": [
                {"source_ref": "SRC-ITR-001", "source_type": "ITR", "source_id": "ITR-R2-W2-001"}
            ],
        },
        "fields": {
            "lifecycle_stage": {
                "value": "运行执行",
                "source_type": "FACT",
                "evidence_ids": ["itr.phase"],
                "confidence": 0.95,
                "review_status": "CONFIRMED",
            },
            "business_activity_scene": {
                "value": "掉电数据保持与上电恢复",
                "source_type": "FACT",
                "evidence_ids": ["itr.activity"],
                "confidence": 0.95,
                "review_status": "CONFIRMED",
            },
            "customer_experience": {
                "value": "掉电后关键计数丢失",
                "source_type": "FACT",
                "evidence_ids": ["itr.symptom"],
                "confidence": 0.9,
                "review_status": "CONFIRMED",
            },
            "quality_risk": {
                "value": "数据完整性",
                "source_type": "INFERRED",
                "evidence_ids": ["itr.symptom"],
                "confidence": 0.8,
                "review_status": "PENDING",
            },
            "expected_quality_state": {
                "value": "重新上电后关键数据正确恢复",
                "source_type": "INFERRED",
                "evidence_ids": ["itr.solution"],
                "confidence": 0.85,
                "review_status": "PENDING",
            },
            "trigger_condition": {
                "value": "运行中异常掉电",
                "source_type": "FACT",
                "evidence_ids": ["itr.trigger"],
                "confidence": 0.95,
                "review_status": "CONFIRMED",
            },
        },
        "missing_information": [],
    }


def taxonomy():
    return {
        "version_id": "STV-R2-W2-1",
        "lifecycles": [
            {
                "lifecycle_code": "RUNTIME_EXECUTION",
                "label_zh": "运行执行",
                "enabled": 1,
            }
        ],
        "activities": [
            {
                "activity_code": "POWER_LOSS_RETENTION_RECOVERY",
                "lifecycle_code": "RUNTIME_EXECUTION",
                "label_zh": "掉电数据保持与上电恢复",
                "objective": "保证掉电后关键数据正确恢复",
                "chain_text": "运行 → 掉电 → 上电 → 恢复",
                "enabled": 1,
            }
        ],
    }


def test_r2_w2_qsv1_candidate_review_confirm_publish_traceability(tmp_path: Path):
    db = tmp_path / "qsv1.db"
    app = FastAPI()
    app.include_router(create_quality_scenario_v1_router(str(db)))
    client = TestClient(app)

    created = client.post(
        PREFIX + "/quality-scenarios/candidates/from-reverse",
        json={
            "reverse_quality_result": reverse_result(),
            "taxonomy": taxonomy(),
            "trigger_source": "HIGH_PERCEPTION",
            "trigger_reason": "客户高感知问题",
            "created_by": "R2_W2",
        },
    )
    assert created.status_code == 200, created.text
    scenario = created.json()["scenario"]
    scenario_id = scenario["scenario_id"]
    assert scenario["status"] == "CANDIDATE"
    assert scenario["source_problem_refs"]
    assert scenario["evidence_refs"]
    assert any(ref["source_type"] == "SCENARIO_SOURCE_BUNDLE" for ref in scenario["source_problem_refs"])
    assert any(ref["source_type"] == "SELECTED_ISSUE" for ref in scenario["source_problem_refs"])
    provenance_evidence = next(item for item in scenario["evidence_refs"] if item["evidence_type"] == "QSV1_PRODUCTION_PROVENANCE")
    assert "SSB-R2-001/BUNDLE-R1" in provenance_evidence["content_ref"]

    # A fresh analysis run against the same frozen bundle reuses the candidate.
    repeated = client.post(
        PREFIX + "/quality-scenarios/candidates/from-reverse",
        json={
            "reverse_quality_result": reverse_result(run="RQRUN-R2-W2-RETRY"),
            "taxonomy": taxonomy(),
            "trigger_source": "HIGH_PERCEPTION",
            "trigger_reason": "客户高感知问题",
        },
    )
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["created"] is False
    assert repeated.json()["scenario"]["scenario_id"] == scenario_id

    candidates = client.get(PREFIX + "/quality-scenarios/candidates")
    assert candidates.status_code == 200
    assert candidates.json()["total"] == 1

    reviewed = client.post(
        PREFIX + f"/quality-scenarios/{scenario_id}/review",
        json={
            "expected_scenario_version": scenario["scenario_version"],
            "patch": {"scenario_description": "R2 人工确认后的掉电恢复场景"},
            "review_status": "CONFIRMED",
            "reviewer": "QUALITY_OWNER",
            "comment": "Review 完成",
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    reviewed_scenario = reviewed.json()["scenario"]
    assert reviewed_scenario["scenario_version"] == 2

    confirmed = client.post(
        PREFIX + f"/quality-scenarios/{scenario_id}/confirm",
        json={
            "expected_scenario_version": reviewed_scenario["scenario_version"],
            "quality_confirmed_by": "QUALITY_OWNER",
            "technical_confirmed_by": "RND_OWNER",
            "confirmation_note": "质量与研发共同确认",
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    confirmed_scenario = confirmed.json()["scenario"]
    assert confirmed_scenario["status"] == "CONFIRMED"

    published = client.post(
        PREFIX + f"/quality-scenarios/{scenario_id}/publish",
        json={
            "expected_scenario_version": confirmed_scenario["scenario_version"],
            "published_by": "QUALITY_OWNER",
        },
    )
    assert published.status_code == 200, published.text
    final = published.json()["scenario"]
    assert final["status"] == "PUBLISHED"
    assert final["scenario_version"] == 4

    listing = client.get(PREFIX + "/quality-scenarios", params={"status": "PUBLISHED"})
    assert listing.status_code == 200
    assert listing.json()["total"] == 1

    history = client.get(PREFIX + f"/quality-scenarios/{scenario_id}/history")
    assert history.status_code == 200
    assert len(history.json()["versions"]) == 4

    trace = client.get(PREFIX + f"/quality-scenarios/{scenario_id}/traceability")
    assert trace.status_code == 200
    assert trace.json()["scenario_id"] == scenario_id
    assert trace.json()["integrity"]["status"] == "PASS"
    assert trace.json()["sources"]
    assert trace.json()["evidence"]

    # New source revision creates a new lineage and leaves published history intact.
    revised = client.post(
        PREFIX + "/quality-scenarios/candidates/from-reverse",
        json={
            "reverse_quality_result": reverse_result(revision="BUNDLE-R2", run="RQRUN-R2-W2-R2"),
            "taxonomy": taxonomy(),
            "trigger_source": "HIGH_PERCEPTION",
            "trigger_reason": "客户高感知问题",
        },
    )
    assert revised.status_code == 200, revised.text
    assert revised.json()["created"] is True
    assert revised.json()["scenario"]["scenario_id"] != scenario_id
    assert client.get(PREFIX + f"/quality-scenarios/{scenario_id}").json()["status"] == "PUBLISHED"
    assert len(client.get(PREFIX + f"/quality-scenarios/{scenario_id}/history").json()["versions"]) == 4


def test_w3_candidate_requires_w2_bundle_provenance(tmp_path: Path):
    app = FastAPI()
    app.include_router(create_quality_scenario_v1_router(str(tmp_path / "missing-provenance.db")))
    response = TestClient(app).post(
        PREFIX + "/quality-scenarios/candidates/from-reverse",
        json={
            "reverse_quality_result": {
                "result_version": "reverse-quality-v0.1",
                "analysis_id": "A1",
                "run_id": "R1",
                "run_seq": 1,
                "identity": {"canonical_itr": "ITR-1", "product_code": "PLC", "taxonomy_version_id": "STV-R2-W2-1"},
                "fields": {},
            },
            "taxonomy": taxonomy(),
            "trigger_source": "HIGH_PERCEPTION",
            "trigger_reason": "test",
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "QSV1_BUNDLE_PROVENANCE_REQUIRED"


def test_w3_ambiguous_existing_bundle_identity_fails_closed(tmp_path: Path):
    repository = SQLiteQualityScenarioV1Repository(tmp_path / "ambiguous.db")
    service = CandidateV1Service(repository)
    first = service.create_from_reverse(
        reverse_result(), taxonomy(), trigger_source="HIGH_PERCEPTION", trigger_reason="customer issue"
    )
    duplicate = first.scenario.model_copy(update={"scenario_id": "QSV1C-MANUAL-DUPLICATE"})
    repository.save(duplicate)
    with pytest.raises(ValueError, match="QSV1_CANDIDATE_PROVENANCE_AMBIGUOUS"):
        service.create_from_reverse(
            reverse_result(run="RQRUN-RETRY"), taxonomy(),
            trigger_source="HIGH_PERCEPTION", trigger_reason="customer issue",
        )


def test_w3_concurrent_same_bundle_creation_is_atomic(tmp_path: Path):
    repository = SQLiteQualityScenarioV1Repository(tmp_path / "concurrent.db")
    service = CandidateV1Service(repository)

    def create(_):
        return service.create_from_reverse(
            reverse_result(run=f"RQRUN-CONCURRENT-{_}"),
            taxonomy(),
            trigger_source="HIGH_PERCEPTION",
            trigger_reason="customer issue",
        )

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(create, range(6)))
    assert sum(item.created for item in results) == 1
    assert len({item.scenario.scenario_id for item in results}) == 1
    assert len(repository.list()) == 1


def test_r2_w2_qsv1_namespace_does_not_mutate_legacy_scenario_table(tmp_path: Path):
    db = tmp_path / "coexist.db"
    with sqlite3.connect(db) as connection:
        connection.execute(
            "CREATE TABLE quality_scenario(scenario_id TEXT PRIMARY KEY, name TEXT)"
        )
        connection.execute(
            "INSERT INTO quality_scenario VALUES(?,?)",
            ("LEGACY-1", "旧质量场景"),
        )

    create_quality_scenario_v1_router(str(db))

    with sqlite3.connect(db) as connection:
        legacy = connection.execute(
            "SELECT scenario_id,name FROM quality_scenario"
        ).fetchall()
        qsv1_table = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='quality_scenario_v1'"
        ).fetchone()[0]
    assert legacy == [("LEGACY-1", "旧质量场景")]
    assert qsv1_table == 1


def test_r2_w2_qsv1_api_namespace_can_coexist_with_p04_public_projection(tmp_path: Path):
    app = FastAPI()
    app.include_router(create_quality_scenario_v1_router(str(tmp_path / "coexist-api.db")))

    p04 = APIRouter(prefix="/api/v2/quality-scenarios")
    @p04.get("/{scenario_id}")
    def p04_detail(scenario_id: str):
        return {"contract": "P04", "scenario_id": scenario_id}
    app.include_router(p04)

    client = TestClient(app)
    p04_result = client.get("/api/v2/quality-scenarios/QS-P04-1")
    assert p04_result.status_code == 200
    assert p04_result.json()["contract"] == "P04"

    workflow = client.get(PREFIX + "/quality-scenarios")
    assert workflow.status_code == 200
    assert workflow.json()["total"] == 0


def test_r2_w2_qsv1_pages_keep_workflow_and_p04_details_separate():
    app = FastAPI()
    app.state.overall_shell_enabled = False
    app.include_router(create_p0_insights_router(scenario_detail_service=None))
    client = TestClient(app)

    workbench = client.get("/p0/quality-scenarios/workbench")
    assert workbench.status_code == 200
    assert "场景工作台" in workbench.text
    assert PREFIX in workbench.text

    library = client.get("/p0/quality-scenarios")
    assert library.status_code == 200
    assert "质量场景库" in library.text
    assert PREFIX in library.text

    audit = client.get("/p0/quality-scenarios/library/QSV1-1")
    assert audit.status_code == 200
    assert "QualityScenario V1" in audit.text
    assert PREFIX in audit.text

    # P04 published-scenario detail keeps its original route and does not fall
    # through into the V1 audit page.
    p04 = client.get("/p0/quality-scenarios/QS-P04-1")
    assert p04.status_code == 404
    assert p04.json()["detail"] == "QUALITY_SCENARIO_NOT_FOUND"


def test_r2_w2_library_javascript_uses_separate_v1_audit_detail_route():
    script = (
        Path(__file__).resolve().parents[1]
        / "quality_knowledge/web/static/p0_scenario_library.js"
    ).read_text(encoding="utf-8")
    assert "/p0/quality-scenarios/library/" in script
    assert "window.location.href='/p0/quality-scenarios/'+encodeURIComponent(id)" not in script
