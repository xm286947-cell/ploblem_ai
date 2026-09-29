from __future__ import annotations

import json
from pathlib import Path

import jsonschema
from fastapi.testclient import TestClient

from quality_knowledge.web.p0_app import create_p0_app

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads(
    (ROOT / "schema/hardware_public_consumer_v1.schema.json").read_text(encoding="utf-8")
)


def _field(value: str):
    return {
        "candidate_value": value,
        "confirmed_value": value,
        "review_disposition": "CONFIRMED",
        "evidence_refs": ["EV-PUBLIC-1"],
    }


def test_public_http_binding_golden_path(tmp_path):
    app = create_p0_app(
        tmp_path / "quality.db",
        hardware_case_db_path=tmp_path / "hardware.db",
        hardware_tree_upload_dir=tmp_path / "tree",
        hardware_case_source_root=tmp_path / "sources",
        enabled_domains={"HARDWARE_CASE"},
    )
    service = app.state.hardware_case_service

    service.create_case(
        {
            "case_id": "HC-PUBLIC-HTTP-1",
            "title": "Public HTTP Case",
            "case_status": "PENDING_REVIEW",
            "processing_status": "READY",
            "source_refs": ["word:public-http.docx"],
            "product_context": {"product": "Controller"},
            "facts": {
                "symptom": _field("启动失败"),
                "root_cause": _field("输入浪涌"),
                "actions": _field("增加保护"),
            },
        }
    )
    service.save_tree_node(
        {
            "node_id": "CF-PUBLIC-1",
            "tree_type": "CIRCUIT_FEATURE",
            "name": "DC/DC",
            "path": ["电源", "DC/DC"],
            "active": True,
        }
    )
    service.save_evidence(
        {
            "evidence_id": "EV-PUBLIC-1",
            "case_id": "HC-PUBLIC-HTTP-1",
            "source_ref": "word:public-http.docx",
            "evidence_type": "TEXT",
            "locator": {"paragraph": 1},
            "excerpt_or_caption": "输入浪涌导致启动失败，增加保护",
            "evidence_status": "AVAILABLE",
        }
    )
    service.set_mapping(
        {
            "mapping_id": "MAP-PUBLIC-1",
            "case_id": "HC-PUBLIC-HTTP-1",
            "tree_type": "CIRCUIT_FEATURE",
            "node_id": "CF-PUBLIC-1",
            "relation_role": "PRIMARY",
            "mapping_status": "CONFIRMED",
            "confidence": 1.0,
            "basis_refs": ["EV-PUBLIC-1"],
        }
    )
    published = service.publish_case("HC-PUBLIC-HTTP-1")
    assert published["case_status"] == "PUBLISHED"

    client = TestClient(app)
    responses = [
        client.get("/api/public/hardware/v1/cases?q=启动失败"),
        client.get("/api/public/hardware/v1/cases/HC-PUBLIC-HTTP-1"),
        client.get("/api/public/hardware/v1/trees/CIRCUIT_FEATURE"),
        client.get("/api/public/hardware/v1/tree-nodes/CF-PUBLIC-1/cases"),
        client.get("/api/public/hardware/v1/cases/HC-PUBLIC-HTTP-1/mappings"),
        client.get("/api/public/hardware/v1/cases/HC-PUBLIC-HTTP-1/evidence"),
    ]
    for response in responses:
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["public_contract_version"] == "hardware-public-consumer/v1"
        jsonschema.validate(payload, SCHEMA)


def test_public_http_binding_is_read_only_and_fail_closed(tmp_path):
    app = create_p0_app(
        tmp_path / "quality.db",
        hardware_case_db_path=tmp_path / "hardware.db",
        hardware_tree_upload_dir=tmp_path / "tree",
        hardware_case_source_root=tmp_path / "sources",
        enabled_domains={"HARDWARE_CASE"},
    )
    service = app.state.hardware_case_service
    service.create_case(
        {
            "case_id": "HC-UNPUBLISHED-1",
            "title": "Unpublished",
            "case_status": "PENDING_REVIEW",
            "processing_status": "READY",
            "source_refs": [],
            "product_context": {},
            "facts": {},
        }
    )

    client = TestClient(app)
    assert client.get("/api/public/hardware/v1/cases").json()["payload"]["results"] == []
    assert client.get("/api/public/hardware/v1/cases/HC-UNPUBLISHED-1").status_code == 404
    assert client.post("/api/public/hardware/v1/cases", json={}).status_code == 405
