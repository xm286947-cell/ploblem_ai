from __future__ import annotations

from io import BytesIO
from pathlib import Path
import json

import openpyxl
from fastapi.testclient import TestClient

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.web.p0_app import create_p0_app


MAINTAINER = {
    "X-Hardware-Case-Role": "MAINTAINER",
    "X-Hardware-Case-Operator": "maintainer-01",
}


def _workbook_bytes() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "分类"
    ws.append(["说明"])
    ws.append(["编码", "一级", "二级", "三级", "备注"])
    ws.append(["K-USB", "连接器", "特殊连接器", "USB", "接口"])
    ws.append(["K-HDMI", "连接器", "特殊连接器", "HDMI", "视频"])
    out = BytesIO()
    wb.save(out)
    wb.close()
    return out.getvalue()


def _client(tmp_path: Path) -> TestClient:
    root = Path(__file__).resolve().parents[1]
    p0_db = tmp_path / "quality_capability_p0.db"
    hardware_db = tmp_path / "hardware_case_mvp.db"
    upload_dir = tmp_path / "tree_uploads"

    initializer = P0Initializer(
        manifest_path=root / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=root / "quality_knowledge/config/plc_fields.yaml",
    )
    initializer.initialize(p0_db)

    app = create_p0_app(
        p0_db,
        stage_runner=object(),
        hardware_case_db_path=hardware_db,
        hardware_tree_upload_dir=upload_dir,
    )
    return TestClient(app)


def test_m3a_tree_import_requires_maintainer(tmp_path: Path):
    client = _client(tmp_path)
    response = client.post(
        "/api/v2/hardware-cases/tree-imports",
        data={"tree_type": "CIRCUIT_FEATURE"},
        files={
            "file": (
                "circuit.xlsx",
                _workbook_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "HARDWARE_CASE_MAINTAINER_REQUIRED"


def test_m3a_upload_analyze_review_apply_and_consume_through_unified_app(tmp_path: Path):
    client = _client(tmp_path)

    assert client.get("/api/v2/initialization/status").status_code == 200
    assert client.get("/api/v2/products").status_code == 200

    upload = client.post(
        "/api/v2/hardware-cases/tree-imports",
        data={"tree_type": "CIRCUIT_FEATURE"},
        files={
            "file": (
                "circuit.xlsx",
                _workbook_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        headers=MAINTAINER,
    )
    assert upload.status_code == 201, upload.text
    upload_body = upload.json()
    job_id = upload_body["job"]["job_id"]
    assert upload_body["job"]["status"] == "UPLOADED"
    assert upload_body["job"]["import_type"] == "INITIAL_IMPORT"
    assert upload_body["workbook"]["filename"] == "circuit.xlsx"
    assert upload_body["workbook"]["sheets"][0]["sheet_name"] == "分类"
    assert str(tmp_path) not in json.dumps(upload_body, ensure_ascii=False)

    preview = client.post(
        f"/api/v2/hardware-cases/tree-imports/{job_id}/workbook-preview",
        json={"sheet_name": "分类", "header_row": 2, "max_rows": 10},
        headers=MAINTAINER,
    )
    assert preview.status_code == 200, preview.text
    preview_body = preview.json()
    assert [item["column_name"] for item in preview_body["columns"]] == [
        "编码", "一级", "二级", "三级", "备注"
    ]
    assert preview_body["rows"][0]["row_number"] == 3
    assert preview_body["rows"][0]["values"][:4] == [
        "K-USB", "连接器", "特殊连接器", "USB"
    ]
    assert str(tmp_path) not in json.dumps(preview_body, ensure_ascii=False)

    analyze = client.post(
        f"/api/v2/hardware-cases/tree-imports/{job_id}/analyze",
        json={
            "sheet_name": "分类",
            "header_row": 2,
            "path_columns": ["一级", "二级", "三级"],
            "metadata_columns": ["备注"],
            "business_key_column": "编码",
        },
        headers=MAINTAINER,
    )
    assert analyze.status_code == 200, analyze.text
    analysis = analyze.json()
    assert analysis["job"]["status"] == "REVIEW_REQUIRED"
    assert analysis["change_summary"]["ADD"] == 4
    assert analysis["change_summary"]["CONFLICT"] == 0
    assert analysis["preview"]["stats"]["max_depth"] == 3

    for change in analysis["changes"]:
        if change["change_type"] in {"ADD", "UPDATE", "RENAME", "MOVE", "DEPRECATE"}:
            decision = client.post(
                f"/api/v2/hardware-cases/tree-imports/{job_id}"
                f"/changes/{change['change_id']}/decision",
                json={"decision": "CONFIRMED"},
                headers=MAINTAINER,
            )
            assert decision.status_code == 200, decision.text

    ready = client.post(
        f"/api/v2/hardware-cases/tree-imports/{job_id}/ready",
        headers=MAINTAINER,
    )
    assert ready.status_code == 200, ready.text
    assert ready.json()["status"] == "READY_TO_APPLY"

    applied = client.post(
        f"/api/v2/hardware-cases/tree-imports/{job_id}/apply",
        headers=MAINTAINER,
    )
    assert applied.status_code == 200, applied.text
    applied_body = applied.json()
    assert applied_body["job"]["status"] == "APPLIED"
    assert applied_body["active_version"]["version_id"] == "C-001"

    tree = client.get("/api/v2/hardware-cases/trees/CIRCUIT_FEATURE")
    assert tree.status_code == 200
    paths = {tuple(node["path"]) for node in tree.json()["nodes"]}
    assert paths == {
        ("连接器",),
        ("连接器", "特殊连接器"),
        ("连接器", "特殊连接器", "USB"),
        ("连接器", "特殊连接器", "HDMI"),
    }

    version = client.get(
        "/api/v2/hardware-cases/tree-imports/active-version/CIRCUIT_FEATURE",
        headers=MAINTAINER,
    )
    assert version.status_code == 200
    assert version.json()["active_version"]["version_id"] == "C-001"

    history = client.get(
        "/api/v2/hardware-cases/tree-imports?tree_type=CIRCUIT_FEATURE",
        headers=MAINTAINER,
    )
    assert history.status_code == 200
    assert history.json()["total"] == 1
    assert history.json()["items"][0]["job_id"] == job_id

    detail = client.get(
        f"/api/v2/hardware-cases/tree-imports/{job_id}",
        headers=MAINTAINER,
    )
    assert detail.status_code == 200
    serialized = json.dumps(detail.json(), ensure_ascii=False)
    assert str(tmp_path) not in serialized
    assert "circuit.xlsx" in serialized


def test_m3a_operator_is_required_for_auditable_import(tmp_path: Path):
    client = _client(tmp_path)
    response = client.post(
        "/api/v2/hardware-cases/tree-imports",
        data={"tree_type": "CIRCUIT_FEATURE"},
        files={
            "file": (
                "circuit.xlsx",
                _workbook_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        headers={"X-Hardware-Case-Role": "MAINTAINER"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "HARDWARE_TREE_OPERATOR_REQUIRED"
