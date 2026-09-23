from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.web.p0_app import create_p0_app


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAINTAINER = {"X-Hardware-Case-Role": "MAINTAINER"}


def _initializer() -> P0Initializer:
    return P0Initializer(
        manifest_path=PROJECT_ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=PROJECT_ROOT / "quality_knowledge/config/plc_fields.yaml",
    )


def _field(candidate: str):
    return {
        "candidate_value": candidate,
        "confirmed_value": None,
        "review_disposition": "UNREVIEWED",
        "evidence_refs": [],
    }


def test_hardware_case_synthetic_golden_path_on_unified_p0_app(tmp_path):
    p0_db = tmp_path / "quality_capability_p0.db"
    hardware_db = tmp_path / "hardware_case_mvp.db"
    _initializer().initialize(p0_db)

    client = TestClient(
        create_p0_app(
            p0_db,
            stage_runner=object(),
            hardware_case_db_path=hardware_db,
        )
    )

    # Existing platform entrypoints stay alive.
    init = client.get("/api/v2/initialization/status")
    assert init.status_code == 200
    assert init.json()["initialization_state"] == "READY"
    assert client.get("/api/v2/products").status_code == 200
    assert client.get("/", follow_redirects=False).headers["location"] == "/p0/insights"

    # Hardware Case starts on the same /api/v2 surface.
    case_payload = {
        "case_id": "HC-E2E-001",
        "title": "Synthetic DC/DC startup reset",
        "case_status": "PENDING_REVIEW",
        "processing_status": "READY",
        "source_refs": ["word:synthetic-e2e.docx"],
        "product_context": {
            "product": "Synthetic Controller",
            "device_name": "PMIC",
            "device_model": "PMIC-SYNTH",
        },
        "facts": {
            "symptom": _field("上电后控制器反复复位"),
            "root_cause": _field("输入浪涌触发保护"),
            "actions": _field("增加输入保护与浪涌抑制"),
        },
    }
    created = client.post(
        "/api/v2/hardware-cases",
        headers=MAINTAINER,
        json=case_payload,
    )
    assert created.status_code == 201, created.text

    # Consumer cannot see candidate/unpublished knowledge.
    assert client.get("/api/v2/hardware-cases?q=浪涌").json()["results"] == []
    assert client.get("/api/v2/hardware-cases/HC-E2E-001").status_code == 404

    # Tree is mounted in the same application.
    tree = {
        "node_id": "CF-E2E-POWER",
        "tree_type": "CIRCUIT_FEATURE",
        "name": "输入保护",
        "path": ["电源", "输入保护"],
        "description": "Synthetic node",
        "source_ref": "synthetic:circuit-tree.xlsx",
        "active": True,
    }
    assert client.post(
        "/api/v2/hardware-cases/trees/nodes",
        headers=MAINTAINER,
        json=tree,
    ).status_code == 201

    # Human review confirms only the frozen core facts.
    for field_name, value in [
        ("symptom", "上电后控制器反复复位"),
        ("root_cause", "输入浪涌触发保护"),
        ("actions", "增加输入保护与浪涌抑制"),
    ]:
        response = client.post(
            "/api/v2/hardware-cases/HC-E2E-001/review",
            headers=MAINTAINER,
            json={
                "field_name": field_name,
                "disposition": "CONFIRMED",
                "confirmed_value": value,
            },
        )
        assert response.status_code == 200, response.text

    evidence = {
        "evidence_id": "EV-E2E-001",
        "source_ref": "word:synthetic-e2e.docx",
        "evidence_type": "TEXT",
        "locator": {
            "section": "原因分析",
            "paragraph": 8,
            "block_id": "B0008",
        },
        "excerpt_or_caption": "输入浪涌触发保护",
        "evidence_status": "AVAILABLE",
    }
    assert client.post(
        "/api/v2/hardware-cases/HC-E2E-001/evidence",
        headers=MAINTAINER,
        json=evidence,
    ).status_code == 201

    mapping = {
        "mapping_id": "MAP-E2E-001",
        "tree_type": "CIRCUIT_FEATURE",
        "node_id": "CF-E2E-POWER",
        "relation_role": "PRIMARY",
        "mapping_status": "CONFIRMED",
        "confidence": 1.0,
        "basis_refs": ["EV-E2E-001"],
    }
    assert client.post(
        "/api/v2/hardware-cases/HC-E2E-001/mappings",
        headers=MAINTAINER,
        json=mapping,
    ).status_code == 201

    gate = client.get(
        "/api/v2/hardware-cases/HC-E2E-001/publish-gate",
        headers=MAINTAINER,
    )
    assert gate.status_code == 200
    assert gate.json()["passed"] is True
    assert gate.json()["circuit_mapping_state"] == "CONFIRMED"
    assert gate.json()["material_mapping_state"] == "UNMAPPED"

    publish = client.post(
        "/api/v2/hardware-cases/HC-E2E-001/publish",
        headers=MAINTAINER,
    )
    assert publish.status_code == 200
    assert publish.json()["case_status"] == "PUBLISHED"

    # Consumer Golden Path after publish.
    search = client.get("/api/v2/hardware-cases?q=浪涌")
    assert search.status_code == 200
    assert search.json()["results"][0]["case_id"] == "HC-E2E-001"

    by_tree = client.get(
        "/api/v2/hardware-cases/tree-nodes/CF-E2E-POWER/cases"
    )
    assert by_tree.status_code == 200
    assert by_tree.json()["case_count"] == 1

    detail = client.get("/api/v2/hardware-cases/HC-E2E-001")
    assert detail.status_code == 200
    assert detail.json()["facts"]["root_cause"] == "输入浪涌触发保护"
    assert detail.json()["evidence_health"] == "AVAILABLE"

    evidence_view = client.get(
        "/api/v2/hardware-cases/HC-E2E-001/evidence"
    )
    assert evidence_view.status_code == 200
    assert evidence_view.json()["evidence"][0]["locator"]["block_id"] == "B0008"

    # Domain persistence stays separate from the platform control database.
    assert p0_db.exists()
    assert hardware_db.exists()
    assert p0_db != hardware_db
