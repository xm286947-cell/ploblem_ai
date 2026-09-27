from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.web.overall_testability import OverallTestabilityError
from quality_knowledge.web.p0_app import create_p0_app


ROOT = Path(__file__).resolve().parents[1]
TOKEN = "overall-testability-secret"
AUTH = {"X-Overall-Testability-Token": TOKEN}


def _initialized_db(tmp_path: Path) -> Path:
    db = tmp_path / "quality_capability_p1.db"
    P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    ).initialize(db)
    return db


def _app(tmp_path: Path, *, enabled: bool):
    db = _initialized_db(tmp_path)
    return create_p0_app(
        db,
        stage_runner=object(),
        hardware_case_db_path=tmp_path / "hardware_case_mvp.db",
        hardware_tree_upload_dir=tmp_path / "tree_uploads",
        hardware_case_source_root=tmp_path / "hardware_sources",
        testability_enabled=enabled,
        testability_token=TOKEN if enabled else None,
        testability_state_root=tmp_path if enabled else None,
    )


def _fixture_payload():
    return {
        "source_context": {
            "business_type": "PLC",
            "rows": [
                {
                    "ITR单号": "OVERALL-S09-001",
                    "问题描述": "Overall Browser S09 Source Context",
                }
            ],
        },
        "hardware_tree_nodes": [
            {
                "node_id": "OT-FIX-CIRCUIT-001",
                "tree_type": "CIRCUIT_FEATURE",
                "name": "Overall Test Circuit",
                "path": ["Overall Test", "Circuit"],
                "active": True,
            },
            {
                "node_id": "OT-FIX-MATERIAL-001",
                "tree_type": "MATERIAL_DEVICE",
                "name": "Overall Test Device",
                "path": ["Overall Test", "Device"],
                "active": True,
            },
        ],
    }


def test_testability_routes_are_disabled_by_default(tmp_path):
    client = TestClient(_app(tmp_path, enabled=False))
    assert client.get("/api/v2/testability/status", headers=AUTH).status_code == 404


def test_testability_requires_control_token(tmp_path):
    client = TestClient(_app(tmp_path, enabled=True))
    assert client.get("/api/v2/testability/status").status_code == 403
    status = client.get("/api/v2/testability/status", headers=AUTH)
    assert status.status_code == 200
    assert status.json()["contract_version"] == "overall-testability/v1"
    assert status.json()["direct_db_write"] is False
    assert status.json()["direct_repository_write"] is False
    assert status.json()["production_data_touch"] is False


def test_provision_assert_reset_source_context_and_hardware_tree(tmp_path):
    client = TestClient(_app(tmp_path, enabled=True))
    payload = _fixture_payload()

    provision = client.post(
        "/api/v2/testability/fixtures/S09/provision",
        json=payload,
        headers=AUTH,
    )
    assert provision.status_code == 200, provision.text
    body = provision.json()
    assert body["state"] == "READY"
    assert body["passed"] is True
    assert body["checks"]["source_context"]["passed"] is True
    assert body["checks"]["hardware_tree"]["passed"] is True
    assert body["idempotent"] is True
    assert body["repeatable"] is True
    assert body["isolated"] is True
    assert body["production_data_touch"] is False

    issues = client.get("/api/v2/issues", params={"q": "OVERALL-S09-001"})
    assert issues.status_code == 200
    assert issues.json()["total"] == 1

    circuit = client.get("/api/v2/hardware-cases/trees/CIRCUIT_FEATURE")
    material = client.get("/api/v2/hardware-cases/trees/MATERIAL_DEVICE")
    assert circuit.status_code == material.status_code == 200
    assert {item["node_id"] for item in circuit.json()["nodes"]} == {
        "OT-FIX-CIRCUIT-001"
    }
    assert {item["node_id"] for item in material.json()["nodes"]} == {
        "OT-FIX-MATERIAL-001"
    }

    asserted = client.get(
        "/api/v2/testability/fixtures/S09/assert",
        headers=AUTH,
    )
    assert asserted.status_code == 200
    assert asserted.json()["passed"] is True

    reset = client.post(
        "/api/v2/testability/fixtures/S09/reset",
        headers=AUTH,
    )
    assert reset.status_code == 200
    assert reset.json()["clean"] is True

    assert client.get(
        "/api/v2/issues", params={"q": "OVERALL-S09-001"}
    ).json()["total"] == 0
    assert client.get(
        "/api/v2/hardware-cases/trees/CIRCUIT_FEATURE"
    ).json()["nodes"] == []
    assert client.get(
        "/api/v2/hardware-cases/trees/MATERIAL_DEVICE"
    ).json()["nodes"] == []


def test_repeated_provision_is_deterministic_not_accumulative(tmp_path):
    client = TestClient(_app(tmp_path, enabled=True))
    payload = _fixture_payload()

    first = client.post(
        "/api/v2/testability/fixtures/REPEATABLE/provision",
        json=payload,
        headers=AUTH,
    )
    second = client.post(
        "/api/v2/testability/fixtures/REPEATABLE/provision",
        json=payload,
        headers=AUTH,
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["payload_sha256"] == second.json()["payload_sha256"]
    assert second.json()["passed"] is True

    issues = client.get("/api/v2/issues", params={"q": "OVERALL-S09-001"}).json()
    assert issues["total"] == 1
    circuit = client.get("/api/v2/hardware-cases/trees/CIRCUIT_FEATURE").json()
    assert len(circuit["nodes"]) == 1


def test_testability_refuses_state_paths_outside_isolated_root(tmp_path):
    db_root = tmp_path / "dut"
    db_root.mkdir()
    state_root = tmp_path / "isolated"
    state_root.mkdir()
    db = _initialized_db(db_root)

    with pytest.raises(OverallTestabilityError, match="PATH_OUTSIDE_STATE_ROOT"):
        create_p0_app(
            db,
            stage_runner=object(),
            hardware_case_db_path=db_root / "hardware.db",
            testability_enabled=True,
            testability_token=TOKEN,
            testability_state_root=state_root,
        )


def test_adapter_has_no_direct_sql_or_repository_write_path():
    source = (
        ROOT / "quality_knowledge/web/overall_testability.py"
    ).read_text(encoding="utf-8")
    assert ".execute(" not in source
    assert ".connect(" not in source
    assert "sqlite3" not in source
    assert "FastAPI(" not in source
