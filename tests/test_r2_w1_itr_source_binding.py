from pathlib import Path

from fastapi.testclient import TestClient

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.p0.repository import P0Repository
from quality_knowledge.web.p0_app import create_p0_app


ROOT = Path(__file__).resolve().parents[1]


def _repo(tmp_path: Path) -> tuple[Path, P0Repository]:
    db = tmp_path / "p0.db"
    P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    ).initialize(db)
    return db, P0Repository(db)


def _save(repository: P0Repository, *, knowledge_id: str, business_id: str) -> None:
    repository.save_issue(
        knowledge_id=knowledge_id,
        business_issue_id=business_id,
        raw_json={
            "ITR单号": business_id,
            "问题描述": "现场设备通信异常",
            "现场恢复": "已通过临时参数恢复业务",
        },
        normalized_snapshot={
            "ISSUE_FACT": {
                "business_issue_id": business_id,
                "description": "现场设备通信异常",
                "product": "PLC",
                "platform": "IDE",
                "severity": "A",
            }
        },
        mapping_config_id="MAP-PLC-V1",
        mapping_config_version=1,
        standard_catalog_version_id="SFC-PLC-V1",
        source_file_sha256="r2-itr-source",
        sheet_name="issues",
        row_number=1,
    )


def test_itr_detail_exposes_public_source_ref_and_consumed_snapshot(tmp_path: Path):
    db, repository = _repo(tmp_path)
    _save(repository, knowledge_id="K-R2-ITR-1", business_id="itr r2 source 1")

    client = TestClient(create_p0_app(db, stage_runner=object()))
    detail = client.get("/api/v2/issues/K-R2-ITR-1")
    assert detail.status_code == 200
    payload = detail.json()

    assert payload["source_problem_ref"] == {
        "contract_version": "source-problem-itr-ref/v1",
        "ref_type": "ITR",
        "public_ref": "ITRR2SOURCE1",
        "canonical_itr": "ITRR2SOURCE1",
        "source_status": "RESOLVED",
        "source_refs": ["ITRR2SOURCE1"],
    }
    assert payload["source_snapshot"]["version_no"] == 1
    assert payload["source_snapshot"]["updated_at"]

    page = client.get("/p0/issues/K-R2-ITR-1")
    assert page.status_code == 200
    assert "ITR / 现场恢复来源" in page.text
    assert "原现场业务事实仍由 Source Owner 持有" in page.text
    assert "r2-w1-itr-source-binding-v1" in page.text

    js = client.get("/p0/static/p0_issue_detail.js").text
    assert "renderItrSource" in js
    assert "detail.source_problem_ref" in js
    assert "当前消费快照" in js


def test_non_itr_problem_is_not_promoted_to_itr_source_contract(tmp_path: Path):
    db, repository = _repo(tmp_path)
    _save(repository, knowledge_id="K-R2-GENERIC-1", business_id="BUG-R2-1")

    client = TestClient(create_p0_app(db, stage_runner=object()))
    payload = client.get("/api/v2/issues/K-R2-GENERIC-1").json()
    assert payload["source_problem_ref"] is None
    assert payload["source_snapshot"] is None


def test_current_problem_list_marks_itr_as_field_recovery_entry_without_second_route(tmp_path: Path):
    db, repository = _repo(tmp_path)
    _save(repository, knowledge_id="K-R2-ITR-2", business_id="ITR-R2-2")

    client = TestClient(create_p0_app(db, stage_runner=object()))
    page = client.get("/p0/issues")
    assert page.status_code == 200

    js = client.get("/p0/static/p0_issues.js").text
    assert "ITR / 现场恢复" in js
    assert "/p0/issues/" in js

    routes = [route.path for route in client.app.routes]
    assert "/p0/itr" not in routes
    assert "/p0/itr/{knowledge_id}" not in routes
