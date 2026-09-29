from __future__ import annotations

from io import BytesIO
from pathlib import Path

import openpyxl
from fastapi.testclient import TestClient

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.web.p0_app import create_p0_app


ROOT = Path(__file__).resolve().parents[1]
MAINTAINER = {"X-Hardware-Case-Role": "MAINTAINER"}


def _field(value: str) -> dict:
    return {
        "candidate_value": value,
        "confirmed_value": None,
        "review_disposition": "UNREVIEWED",
        "evidence_refs": [],
    }


def _case() -> dict:
    return {
        "case_id": "HC-R2-W3-001",
        "title": "R2 W3 trusted auth audit",
        "case_status": "PENDING_REVIEW",
        "processing_status": "READY",
        "source_refs": ["word:r2-w3.docx"],
        "product_context": {"product": "Synthetic"},
        "facts": {
            "symptom": _field("上电复位"),
            "root_cause": _field("输入浪涌"),
            "actions": _field("增加保护"),
        },
    }


def _hardware_client(tmp_path: Path, role: str) -> TestClient:
    return TestClient(
        create_p0_app(
            tmp_path / "quality.db",
            hardware_case_db_path=tmp_path / "hardware.db",
            hardware_tree_upload_dir=tmp_path / "tree_uploads",
            hardware_case_source_root=tmp_path / "sources",
            enabled_domains={"HARDWARE_CASE"},
            hardware_case_host_role=role,
        )
    )


def _workbook_bytes() -> bytes:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "分类"
    sheet.append(["编码", "一级", "二级"])
    sheet.append(["C-POWER", "电源", "输入保护"])
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def test_consumer_cannot_escalate_from_browser_controlled_state(tmp_path: Path):
    client = _hardware_client(tmp_path, "CONSUMER")

    page = client.get("/p0/hardware-cases?role=maintainer")
    assert page.status_code == 200
    assert "维护视图" not in page.text
    assert "案例确认" not in page.text
    assert "基础数据管理" not in page.text

    assert client.get("/p0/hardware-cases/review?role=maintainer").status_code == 403
    assert client.get("/p0/hardware-cases/intake?role=maintainer").status_code == 403
    assert client.get("/p0/hardware-cases/base-data?role=maintainer").status_code == 403

    api = client.post(
        "/api/v2/hardware-cases",
        json=_case(),
        headers=MAINTAINER,
    )
    assert api.status_code == 403
    assert api.json()["detail"] == "HARDWARE_CASE_MAINTAINER_REQUIRED"

    tree = client.post(
        "/api/v2/hardware-cases/tree-imports",
        data={"tree_type": "CIRCUIT_FEATURE"},
        files={"file": ("tree.xlsx", _workbook_bytes())},
        headers={
            "X-Hardware-Case-Role": "MAINTAINER",
            "X-Hardware-Case-Operator": "browser-spoof",
        },
    )
    assert tree.status_code == 403

    scripts = (
        client.get("/p0/static/hardware_case.js").text
        + client.get("/p0/static/hardware_tree_import.js").text
    )
    assert "localStorage" not in scripts
    assert "sessionStorage" not in scripts


def test_overall_navigation_hides_hardware_maintenance_for_consumer(tmp_path: Path):
    p0_db = tmp_path / "quality_capability_p0.db"
    P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    ).initialize(p0_db)
    client = TestClient(
        create_p0_app(
            p0_db,
            stage_runner=object(),
            hardware_case_db_path=tmp_path / "hardware.db",
            hardware_case_host_role="CONSUMER",
        )
    )
    payload = client.get("/api/v2/overall/product-areas").json()
    base_data = next(
        capability
        for area in payload["items"]
        for capability in area["capabilities"]
        if capability["path"] == "/p0/hardware-cases/base-data"
    )
    assert base_data["available"] is False


def test_confirm_publish_and_mapping_revision_have_trusted_audit(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setenv("HARDWARE_CASE_HOST_ACTOR", "trusted-maintainer-01")
    client = _hardware_client(tmp_path, "MAINTAINER")

    assert client.post(
        "/api/v2/hardware-cases",
        json=_case(),
        headers=MAINTAINER,
    ).status_code == 201
    assert client.post(
        "/api/v2/hardware-cases/trees/nodes",
        headers=MAINTAINER,
        json={
            "node_id": "CF-R2-W3",
            "tree_type": "CIRCUIT_FEATURE",
            "name": "输入保护",
            "path": ["电源", "输入保护"],
            "active": True,
        },
    ).status_code == 201

    for field_name, value in (
        ("symptom", "上电复位"),
        ("root_cause", "输入浪涌"),
        ("actions", "增加保护"),
    ):
        assert client.post(
            "/api/v2/hardware-cases/HC-R2-W3-001/review",
            headers=MAINTAINER,
            json={
                "field_name": field_name,
                "disposition": "CONFIRMED",
                "confirmed_value": value,
            },
        ).status_code == 200

    assert client.post(
        "/api/v2/hardware-cases/HC-R2-W3-001/evidence",
        headers=MAINTAINER,
        json={
            "evidence_id": "EV-R2-W3",
            "source_ref": "word:r2-w3.docx",
            "evidence_type": "TEXT",
            "locator": {"block_id": "B1"},
            "excerpt_or_caption": "上电复位 输入浪涌 增加保护",
            "evidence_status": "AVAILABLE",
        },
    ).status_code == 201

    mapping = {
        "mapping_id": "MAP-R2-W3",
        "tree_type": "CIRCUIT_FEATURE",
        "node_id": "CF-R2-W3",
        "relation_role": "PRIMARY",
        "mapping_status": "CONFIRMED",
        "confidence": 1.0,
        "basis_refs": ["EV-R2-W3"],
    }
    assert client.post(
        "/api/v2/hardware-cases/HC-R2-W3-001/mappings",
        headers=MAINTAINER,
        json=mapping,
    ).status_code == 201
    mapping["confidence"] = 0.9
    assert client.post(
        "/api/v2/hardware-cases/HC-R2-W3-001/mappings",
        headers=MAINTAINER,
        json=mapping,
    ).status_code == 201

    published = client.post(
        "/api/v2/hardware-cases/HC-R2-W3-001/publish",
        headers=MAINTAINER,
    )
    assert published.status_code == 200
    assert published.json()["case_status"] == "PUBLISHED"

    audit = client.get(
        "/api/v2/hardware-cases/maintenance/audit",
        headers=MAINTAINER,
    ).json()["items"]
    actions = [item["action"] for item in audit]
    assert actions.count("CASE_CONFIRM") == 3
    assert "MAPPING_CREATE" in actions
    assert "MAPPING_REVISION" in actions
    assert "CASE_PUBLISH" in actions
    assert all(item["actor"] == "trusted-maintainer-01" for item in audit)
    assert all(item["occurred_at"] for item in audit)


def test_p07_apply_and_deprecate_write_trusted_audit(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HARDWARE_CASE_HOST_ACTOR", "trusted-maintainer-02")
    client = _hardware_client(tmp_path, "MAINTAINER")

    upload = client.post(
        "/api/v2/hardware-cases/tree-imports",
        data={"tree_type": "CIRCUIT_FEATURE"},
        files={"file": ("tree.xlsx", _workbook_bytes())},
        headers={
            "X-Hardware-Case-Role": "MAINTAINER",
            "X-Hardware-Case-Operator": "spoofed-browser-user",
        },
    )
    assert upload.status_code == 201
    assert upload.json()["job"]["operator"] == "trusted-maintainer-02"
    job_id = upload.json()["job"]["job_id"]

    analyzed = client.post(
        f"/api/v2/hardware-cases/tree-imports/{job_id}/analyze",
        headers=MAINTAINER,
        json={
            "sheet_name": "分类",
            "header_row": 1,
            "path_columns": ["一级", "二级"],
            "metadata_columns": [],
            "business_key_column": "编码",
        },
    ).json()
    for change in analyzed["changes"]:
        if change["change_type"] != "NO_CHANGE":
            assert client.post(
                f"/api/v2/hardware-cases/tree-imports/{job_id}/changes/{change['change_id']}/decision",
                headers=MAINTAINER,
                json={"decision": "CONFIRMED"},
            ).status_code == 200
    assert client.post(
        f"/api/v2/hardware-cases/tree-imports/{job_id}/ready",
        headers=MAINTAINER,
    ).status_code == 200
    assert client.post(
        f"/api/v2/hardware-cases/tree-imports/{job_id}/apply",
        headers=MAINTAINER,
    ).status_code == 200

    app = client.app
    tree = client.get("/api/v2/hardware-cases/trees/CIRCUIT_FEATURE").json()["nodes"]
    node = next(item for item in tree if item["path"] == ["电源", "输入保护"])
    repository = app.state.hardware_tree_import_repository
    deprecate_job = "HTI-R2-W3-DEPRECATE"
    repository.create_job(
        job_id=deprecate_job,
        tree_type="CIRCUIT_FEATURE",
        source_filename="deprecate.xlsx",
        source_sha256="a" * 64,
        operator="trusted-maintainer-02",
    )
    repository.advance_status(deprecate_job, "PARSED")
    repository.advance_status(deprecate_job, "VALIDATING")
    repository.advance_status(deprecate_job, "REVIEW_REQUIRED")
    repository.save_change(
        deprecate_job,
        {
            "change_id": "CH-R2-W3-DEPRECATE",
            "change_type": "DEPRECATE",
            "node_id": node["node_id"],
            "business_key": node.get("business_key"),
            "before": node,
            "after": None,
            "decision": "CONFIRMED",
        },
    )
    repository.mark_ready_to_apply(deprecate_job)

    applied = client.post(
        f"/api/v2/hardware-cases/tree-imports/{deprecate_job}/apply",
        headers=MAINTAINER,
    )
    assert applied.status_code == 200

    audit = client.get(
        "/api/v2/hardware-cases/maintenance/audit",
        headers=MAINTAINER,
    ).json()["items"]
    actions = [item["action"] for item in audit]
    assert actions.count("TREE_APPLY") == 2
    deprecations = [item for item in audit if item["action"] == "TREE_DEPRECATE"]
    assert len(deprecations) == 1
    assert deprecations[0]["target_id"] == node["node_id"]
    assert deprecations[0]["actor"] == "trusted-maintainer-02"
