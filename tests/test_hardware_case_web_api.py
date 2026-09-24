from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from quality_knowledge.web.hardware_case_api import create_hardware_case_router
from repositories.hardware_case_repository import HardwareCaseRepository
from services.hardware_case_backend import HardwareCaseBackendService


MAINTAINER = {"X-Hardware-Case-Role": "MAINTAINER"}


def _field(candidate, confirmed=None, disposition="UNREVIEWED", refs=()):
    return {
        "candidate_value": candidate,
        "confirmed_value": confirmed,
        "review_disposition": disposition,
        "evidence_refs": list(refs),
    }


def _case(status="PENDING_REVIEW"):
    return {
        "case_id": "HC-WEB-001",
        "title": "Synthetic Power Case",
        "case_status": status,
        "processing_status": "READY",
        "source_refs": ["word:synthetic.docx"],
        "product_context": {"product": "Synthetic Controller"},
        "facts": {
            "symptom": _field("上电复位"),
            "root_cause": _field("输入浪涌"),
            "actions": _field("增加保护"),
        },
    }


def _client(tmp_path):
    backend = HardwareCaseBackendService(
        HardwareCaseRepository(tmp_path / "hardware_case.sqlite3")
    )
    app = FastAPI()
    app.include_router(create_hardware_case_router(backend))
    return TestClient(app), backend


def _prepare_publishable(client: TestClient):
    assert client.post("/api/v2/hardware-cases", json=_case(), headers=MAINTAINER).status_code == 201
    node = {
        "node_id": "CF-POWER",
        "tree_type": "CIRCUIT_FEATURE",
        "name": "输入保护",
        "path": ["电源", "输入保护"],
        "active": True,
    }
    assert client.post("/api/v2/hardware-cases/trees/nodes", json=node, headers=MAINTAINER).status_code == 201
    for field, value in [
        ("symptom", "上电复位"),
        ("root_cause", "输入浪涌"),
        ("actions", "增加保护"),
    ]:
        response = client.post(
            "/api/v2/hardware-cases/HC-WEB-001/review",
            json={
                "field_name": field,
                "disposition": "CONFIRMED",
                "confirmed_value": value,
            },
            headers=MAINTAINER,
        )
        assert response.status_code == 200
    mapping = {
        "mapping_id": "MAP-WEB-1",
        "tree_type": "CIRCUIT_FEATURE",
        "node_id": "CF-POWER",
        "relation_role": "PRIMARY",
        "mapping_status": "CONFIRMED",
        "confidence": 1.0,
        "basis_refs": ["EV-WEB-1"],
    }
    assert client.post(
        "/api/v2/hardware-cases/HC-WEB-001/mappings",
        json=mapping,
        headers=MAINTAINER,
    ).status_code == 201
    evidence = {
        "evidence_id": "EV-WEB-1",
        "source_ref": "word:synthetic.docx",
        "evidence_type": "TEXT",
        "locator": {"section": "原因分析", "paragraph": 8},
        "excerpt_or_caption": "输入浪涌",
        "evidence_status": "AVAILABLE",
    }
    assert client.post(
        "/api/v2/hardware-cases/HC-WEB-001/evidence",
        json=evidence,
        headers=MAINTAINER,
    ).status_code == 201


def test_consumer_default_cannot_see_unpublished_case(tmp_path):
    client, _ = _client(tmp_path)
    assert client.post(
        "/api/v2/hardware-cases", json=_case(), headers=MAINTAINER
    ).status_code == 201

    assert client.get("/api/v2/hardware-cases?q=上电复位").json()["results"] == []
    assert client.get("/api/v2/hardware-cases/HC-WEB-001").status_code == 404

    maintainer = client.get(
        "/api/v2/hardware-cases/HC-WEB-001", headers=MAINTAINER
    )
    assert maintainer.status_code == 200
    assert maintainer.json()["case_id"] == "HC-WEB-001"


def test_mutations_require_maintainer_role(tmp_path):
    client, _ = _client(tmp_path)
    response = client.post("/api/v2/hardware-cases", json=_case())
    assert response.status_code == 403
    assert response.json()["detail"] == "HARDWARE_CASE_MAINTAINER_REQUIRED"

    bad_role = client.get(
        "/api/v2/hardware-cases",
        headers={"X-Hardware-Case-Role": "ADMIN"},
    )
    assert bad_role.status_code == 403


def test_publish_gate_and_consumer_flow_through_api(tmp_path):
    client, _ = _client(tmp_path)
    _prepare_publishable(client)

    gate = client.get(
        "/api/v2/hardware-cases/HC-WEB-001/publish-gate",
        headers=MAINTAINER,
    )
    assert gate.status_code == 200
    assert gate.json()["passed"] is True

    published = client.post(
        "/api/v2/hardware-cases/HC-WEB-001/publish",
        headers=MAINTAINER,
    )
    assert published.status_code == 200
    assert published.json()["case_status"] == "PUBLISHED"

    search = client.get("/api/v2/hardware-cases?q=输入浪涌")
    assert search.status_code == 200
    assert search.json()["results"][0]["case_id"] == "HC-WEB-001"

    tree_cases = client.get(
        "/api/v2/hardware-cases/tree-nodes/CF-POWER/cases"
    )
    assert tree_cases.status_code == 200
    assert tree_cases.json()["case_count"] == 1

    evidence = client.get(
        "/api/v2/hardware-cases/HC-WEB-001/evidence"
    )
    assert evidence.status_code == 200
    assert evidence.json()["evidence"][0]["evidence_id"] == "EV-WEB-1"


def test_deprecated_is_hidden_by_default_but_available_as_history(tmp_path):
    client, backend = _client(tmp_path)
    backend.create_case(_case(status="DEPRECATED"))

    assert client.get("/api/v2/hardware-cases?q=").json()["results"] == []
    normal = client.get("/api/v2/hardware-cases/HC-WEB-001")
    assert normal.status_code == 404
    historical = client.get(
        "/api/v2/hardware-cases/HC-WEB-001?historical=true"
    )
    assert historical.status_code == 200
    assert historical.json()["case_status"] == "DEPRECATED"


def test_source_unavailable_anomaly_is_maintainer_only(tmp_path):
    client, backend = _client(tmp_path)
    backend.create_case(_case(status="PUBLISHED"))
    backend.save_evidence(
        {
            "evidence_id": "EV-LOST",
            "case_id": "HC-WEB-001",
            "source_ref": "word:synthetic.docx",
            "evidence_type": "TEXT",
            "locator": {"section": "原因"},
            "excerpt_or_caption": "synthetic",
            "evidence_status": "SOURCE_UNAVAILABLE",
        }
    )

    detail = client.get("/api/v2/hardware-cases/HC-WEB-001")
    assert detail.status_code == 200
    assert detail.json()["evidence_health"] == "SOURCE_UNAVAILABLE"

    blocked = client.get("/api/v2/hardware-cases/maintenance/anomalies")
    assert blocked.status_code == 403
    allowed = client.get(
        "/api/v2/hardware-cases/maintenance/anomalies",
        headers=MAINTAINER,
    )
    assert allowed.status_code == 200
    assert allowed.json()["items"] == [
        {"case_id": "HC-WEB-001", "code": "SOURCE_UNAVAILABLE"}
    ]


def test_mapping_read_api_respects_consumer_visibility(tmp_path):
    client, backend = _client(tmp_path)
    _prepare_publishable(client)

    blocked = client.get("/api/v2/hardware-cases/HC-WEB-001/mappings")
    assert blocked.status_code == 404

    maintainer_before_publish = client.get(
        "/api/v2/hardware-cases/HC-WEB-001/mappings",
        headers=MAINTAINER,
    )
    assert maintainer_before_publish.status_code == 200
    assert maintainer_before_publish.json()["mappings"][0]["node_path"] == "电源/输入保护"

    assert client.post(
        "/api/v2/hardware-cases/HC-WEB-001/publish",
        headers=MAINTAINER,
    ).status_code == 200

    consumer = client.get("/api/v2/hardware-cases/HC-WEB-001/mappings")
    assert consumer.status_code == 200
    assert consumer.json()["mappings"][0]["mapping_status"] == "CONFIRMED"

    detail = client.get("/api/v2/hardware-cases/HC-WEB-001")
    assert detail.status_code == 200
    assert detail.json()["mapping_paths"]["CIRCUIT_FEATURE"] == ["电源/输入保护"]
    assert detail.json()["published_at"] is not None
