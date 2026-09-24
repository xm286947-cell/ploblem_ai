"""HTTP Golden Path: two Excel Applies -> Word -> review -> publish -> S1-S4."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.web.p0_app import create_p0_app
from services.hardware_case_scenario_poc import SyntheticSkills


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/golden/hardware_case_scenarios"
MAINTAINER = {"X-Hardware-Case-Role": "MAINTAINER"}
TREE_HEADERS = {**MAINTAINER, "X-Hardware-Case-Operator": "synthetic-reviewer"}
API = "/api/v2/hardware-cases"


def _client(tmp_path, structurer_override=None):
    db = tmp_path / "p0.sqlite3"
    P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    ).initialize(db)
    holder = {}

    def mock_structurer(document):
        if structurer_override is not None:
            return structurer_override(document, holder["app"])
        nodes = holder["app"].state.hardware_case_repository.list_tree_nodes()
        result = SyntheticSkills(
            [node for node in nodes if node["tree_type"] == "CIRCUIT_FEATURE" and node["active"]],
            [node for node in nodes if node["tree_type"] == "MATERIAL_DEVICE" and node["active"]],
        ).structure(document)
        result["circuit_feature_links"] = []
        result["material_links"] = []
        return result

    app = create_p0_app(db, stage_runner=object(), hardware_case_db_path=tmp_path / "hardware.sqlite3", hardware_case_structurer=mock_structurer)
    holder["app"] = app
    return TestClient(app)


def _apply_tree(client, tree_type, filename):
    source = FIXTURES / filename
    upload = client.post(API + "/tree-imports", data={"tree_type": tree_type},
                         files={"file": (filename, source.read_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}, headers=TREE_HEADERS)
    assert upload.status_code == 201, upload.text
    job_id = upload.json()["job"]["job_id"]
    analysis = client.post(API + f"/tree-imports/{job_id}/analyze", json={
        "sheet_name": "Synthetic Tree", "header_row": 1,
        "path_columns": ["level_1", "level_2"], "business_key_column": "node_id",
    }, headers=MAINTAINER)
    assert analysis.status_code == 200, analysis.text
    assert not analysis.json()["change_summary"].get("CONFLICT")
    for change in analysis.json()["changes"]:
        if change["change_type"] in {"ADD", "UPDATE", "RENAME", "MOVE", "DEPRECATE"}:
            decision = client.post(API + f"/tree-imports/{job_id}/changes/{change['change_id']}/decision",
                                   json={"decision": "CONFIRMED"}, headers=MAINTAINER)
            assert decision.status_code == 200, decision.text
    assert client.post(API + f"/tree-imports/{job_id}/ready", headers=MAINTAINER).status_code == 200
    applied = client.post(API + f"/tree-imports/{job_id}/apply", headers=MAINTAINER)
    assert applied.status_code == 200, applied.text
    assert applied.json()["active_version"]["status"] == "ACTIVE"
    assert client.get(API + "/trees/" + tree_type).json()["nodes"]
    return applied.json()["active_version"]["version_id"]


def _upload_and_process(client, case_id):
    word = next(FIXTURES.glob(case_id + "*.docx"))
    upload = client.post(API + "/intakes", files={"file": (word.name, word.read_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}, headers=MAINTAINER)
    assert upload.status_code == 201, upload.text
    item = upload.json()
    assert item["status"] == "UPLOADED"
    process = client.post(API + "/intakes/" + item["intake_id"] + "/process", headers=MAINTAINER)
    assert process.status_code == 200, process.text
    return process.json()


def test_import_golden_path_and_missing_fact_fail_closed(tmp_path):
    client = _client(tmp_path)
    for route in ("/p0/hardware-cases?role=maintainer", "/p0/hardware-cases/base-data", "/p0/hardware-cases/intake"):
        assert client.get(route).status_code == 200
    assert "知识导入" in client.get("/p0/hardware-cases/intake").text
    assert client.get(API + "/intakes").status_code == 403
    word = next(FIXTURES.glob("A9001*.docx"))
    early = client.post(API + "/intakes", files={"file": (word.name, word.read_bytes())}, headers=MAINTAINER)
    early_id = early.json()["intake_id"]
    blocked = client.post(API + "/intakes/" + early_id + "/process", headers=MAINTAINER)
    assert blocked.status_code == 409 and blocked.json()["detail"] == "ACTIVE_TREES_REQUIRED"

    circuit_version = _apply_tree(client, "CIRCUIT_FEATURE", "circuit_feature.xlsx")
    material_version = _apply_tree(client, "MATERIAL_DEVICE", "material.xlsx")
    assert circuit_version != material_version
    item = _upload_and_process(client, "A9001")
    assert item["intake_id"] == early_id  # upload is idempotent for the same Source
    assert item["status"] == "CANDIDATE_READY"
    candidate = item["candidate"]
    assert candidate["case_id"] == "A9001"
    assert all(candidate["facts"][field] for field in ("symptom", "root_cause", "actions"))
    assert candidate["evidence"]
    assert {m["tree_type"] for m in candidate["mappings"]} == {"CIRCUIT_FEATURE", "MATERIAL_DEVICE"}
    assert all(m["mapping_status"] == "SUGGESTED" for m in candidate["mappings"])
    assert client.get(API + "?q=输出振荡").json()["results"] == []

    for field in ("symptom", "root_cause", "actions"):
        saved = client.post(API + "/A9001/review", json={"field_name": field, "disposition": "CONFIRMED", "confirmed_value": candidate["facts"][field]}, headers=MAINTAINER)
        assert saved.status_code == 200, saved.text
    for mapping in candidate["mappings"]:
        saved = client.post(API + "/A9001/mappings", json={**mapping, "mapping_status": "CONFIRMED"}, headers=MAINTAINER)
        assert saved.status_code == 201, saved.text
    gate = client.get(API + "/A9001/publish-gate", headers=MAINTAINER).json()
    assert gate["passed"] is True, gate
    assert client.post(API + "/A9001/publish", headers=MAINTAINER).json()["passed"]
    for mapping in candidate["mappings"]:
        cases = client.get(API + "/tree-nodes/" + mapping["node_id"] + "/cases").json()["results"]
        assert any(case["case_id"] == "A9001" for case in cases)
    assert any(case["case_id"] == "A9001" for case in client.get(API + "?q=输出振荡").json()["results"])
    evidence = client.get(API + "/A9001/evidence").json()["evidence"]
    assert evidence
    preview = client.get(API + "/A9001/evidence/" + evidence[0]["evidence_id"] + "/source-preview")
    assert preview.status_code == 200, preview.text

    for case_id, missing in (("A9008", "root_cause"), ("A9009", "actions")):
        result = _upload_and_process(client, case_id)
        assert result["status"] == "NEEDS_REVIEW"
        assert result["candidate"]["facts"][missing] is None
        assert client.get(API + "/" + case_id + "/publish-gate", headers=MAINTAINER).json()["passed"] is False
        assert client.get(API + "/" + case_id).status_code == 404


def test_failed_processing_retries_same_source_without_publishing(tmp_path):
    attempts = []

    def fail_once(document, app):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("synthetic provider unavailable")
        nodes = app.state.hardware_case_repository.list_tree_nodes()
        return SyntheticSkills(
            [n for n in nodes if n["tree_type"] == "CIRCUIT_FEATURE" and n["active"]],
            [n for n in nodes if n["tree_type"] == "MATERIAL_DEVICE" and n["active"]],
        ).structure(document)

    client = _client(tmp_path, fail_once)
    _apply_tree(client, "CIRCUIT_FEATURE", "circuit_feature.xlsx")
    _apply_tree(client, "MATERIAL_DEVICE", "material.xlsx")
    failed = _upload_and_process(client, "A9001")
    assert failed["status"] == "FAILED"
    intake_id = failed["intake_id"]
    assert client.get(API + "?q=输出振荡").json()["results"] == []
    retried = client.post(API + "/intakes/" + intake_id + "/process", headers=MAINTAINER)
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "CANDIDATE_READY"
    word = next(FIXTURES.glob("A9001*.docx"))
    duplicate = client.post(API + "/intakes", files={"file": (word.name, word.read_bytes())}, headers=MAINTAINER)
    assert duplicate.json()["intake_id"] == intake_id
    assert client.get(API + "/intakes", headers=MAINTAINER).json()["total"] == 1
    assert client.get(API + "?q=输出振荡").json()["results"] == []
    assert client.post(API + "/intakes/" + intake_id + "/process", headers=MAINTAINER).status_code == 409
    invalid = client.post(API + "/intakes", files={"file": ("bad.txt", b"invalid")}, headers=MAINTAINER)
    assert invalid.status_code == 400 and invalid.json()["detail"] == "DOCX_ONLY"
