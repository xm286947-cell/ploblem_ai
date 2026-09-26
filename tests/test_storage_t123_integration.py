from __future__ import annotations

from datetime import datetime, timezone
import sqlite3

from fastapi.testclient import TestClient

from products.storage_rc1.storage_life import core
from products.storage_rc1.storage_life.app import app
from products.storage_rc1.storage_life.engineering_insight import StorageEngineeringInsightService
from products.storage_rc1.storage_life.knowledge_release import KnowledgeReleaseConsumer
from runtime.contracts import (
    RuntimeObservation,
    RuntimeObservationAvailability,
    RuntimeObservationQuality,
)


class _StructuredKnowledge:
    def __init__(self, objects):
        self.objects = objects

    def status(self):
        return {
            "available": True,
            "status": "READY",
            "knowledge_release_version": "TEST-KNOWLEDGE-1",
        }

    def query(self, text, *, device_type="", top_k=8, knowledge_release_version=None):
        objects = [self.objects[0]] if "data_units" in text else [self.objects[-1]]
        return {
            "knowledge_release_version": "TEST-KNOWLEDGE-1",
            "results": objects[:top_k],
        }


def _knowledge_evidence(evidence_id: str) -> dict:
    return {
        "evidence_id": evidence_id,
        "evidence_type": "SOURCE_EXCERPT",
        "source": {
            "source_type": "SPECIFICATION",
            "source_id": "NVME",
            "revision": "2.0d",
        },
        "locator": {"value": {"page": 1, "section": "Health"}},
        "excerpt": "Explicit formal knowledge evidence.",
        "metadata": {"evidence_status": "BOUND"},
    }

def _service(tmp_path, monkeypatch):
    db_path = tmp_path / "storage.sqlite3"
    monkeypatch.setattr(core, "DATA", tmp_path)
    monkeypatch.setattr(core, "DB", db_path)
    return StorageEngineeringInsightService(), db_path


def _observation(observation_id: str, device_id: str) -> RuntimeObservation:
    return RuntimeObservation(
        observation_id=observation_id,
        device_id=device_id,
        device_type="SSD",
        metric_name="data_units_written",
        raw_value=10,
        normalized_value=10,
        unit="data_units",
        capture_time=datetime.now(timezone.utc),
        source_command_or_interface="nvme smart-log /dev/nvme0",
        raw_output_ref="raw://nvme/observation-1",
        evidence_ref="EVIDENCE-RUNTIME-1",
        collector="NVMeSmartAdapter",
        environment={"host": "integration-test"},
        quality_status=RuntimeObservationQuality.VALID,
        availability_status=RuntimeObservationAvailability.AVAILABLE,
        schema_version="1.0",
    )


def test_existing_storage_app_boot_and_exact_integration_routes():
    with TestClient(app) as client:
        health = client.get("/api/health")
        formulas = client.get("/storage/lifetime/formulas")
        missing = client.get("/storage/runtime-observations/does-not-exist")

    assert health.status_code == 200
    assert health.json()["service"] == "storage-life"
    assert formulas.status_code == 200
    assert "NVME_DATA_UNITS_WRITTEN_V1" in {
        item["formula_id"] for item in formulas.json()["items"]
    }
    assert missing.status_code == 404


def test_t1_to_t2_and_t3_use_same_db_and_survive_service_recreation(tmp_path, monkeypatch):
    service, db_path = _service(tmp_path, monkeypatch)
    device_id = "DUT-T123-001"

    observation = service.create_observation(_observation("OBS-T123-001", device_id))
    assert observation.is_formally_consumable

    lifetime_object = {
        "object_id": "KO-NVME-DATA-UNITS-1",
        "status": "ACTIVE",
        "title": "NVMe Data Units Written",
        "knowledge_release_version": "TEST-KNOWLEDGE-1",
        "evidence_refs": ["EVIDENCE-KNOWLEDGE-1"],
        "evidence": [_knowledge_evidence("EVIDENCE-KNOWLEDGE-1")],
        "parameters": {"bytes_per_data_unit": 512000},
    }
    impact_object = {
        "object_id": "KO-PERCENTAGE-IMPACT-1",
        "status": "ACTIVE",
        "title": "Percentage Used impact",
        "knowledge_release_version": "TEST-KNOWLEDGE-1",
        "evidence_refs": ["EVIDENCE-KNOWLEDGE-2"],
        "evidence": [_knowledge_evidence("EVIDENCE-KNOWLEDGE-2")],
        "trigger_fact_names": ["percentage_used"],
        "technical_meaning": "The device reports consumed endurance percentage.",
        "software_impact": ["Review endurance monitoring thresholds."],
        "validation_items": [
            {
                "validation_id": "VAL-1",
                "trigger_ref": "percentage_used",
                "type": "MONITORING",
                "description": "Verify the monitoring threshold.",
                "status": "PENDING",
                "evidence_refs": ["EVIDENCE-KNOWLEDGE-2"],
            }
        ],
    }
    monkeypatch.setattr(
        KnowledgeReleaseConsumer,
        "current",
        classmethod(lambda cls: _StructuredKnowledge([lifetime_object, impact_object])),
    )

    lifetime = service.assess_lifetime(
        {
            "device_id": device_id,
            "device_type": "SSD",
            "metric": "nvme.data_units_written",
            "confirmed_facts": [],
        }
    )
    assert lifetime["status"] == "CALCULATED"
    assert lifetime["result"] == 10 * 512000
    assert set(lifetime["evidence_refs"]) == {
        "EVIDENCE-RUNTIME-1",
        "EVIDENCE-KNOWLEDGE-1",
    }
    assert lifetime["replay_trace"]["knowledge_refs"] == ["KO-NVME-DATA-UNITS-1"]

    impact = service.analyze_impact(
        {
            "request_id": "REQ-T123-001",
            "device_id": device_id,
            "device_type": "SSD",
            "usage_context": {"product": "storage-life"},
            "requested_topics": ["percentage_used"],
            "confirmed_facts": [
                {
                    "fact_id": "FACT-PERCENTAGE-USED-1",
                    "metric_name": "percentage_used",
                    "value": 12,
                    "unit": "%",
                    "evidence_refs": ["EVIDENCE-FACT-1"],
                    "source_type": "CONFIRMED_DEVICE_FACT",
                }
            ],
        }
    )
    assert impact["status"] == "EVIDENCED"
    assert impact["impacts"][0]["validation_items"][0]["trigger_ref"] == "percentage_used"
    assert impact["decision_boundary"] == "NO_AUTO_REPLACEMENT_DECISION"

    recreated = StorageEngineeringInsightService()
    assert recreated.get_lifetime(lifetime["assessment_id"]) == lifetime
    assert recreated.get_impact(impact["analysis_id"]) == impact

    with sqlite3.connect(db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {"runtime_observation", "lifetime_assessment", "software_impact_analysis"} <= tables


def test_production_knowledge_without_structured_impact_fails_closed(tmp_path, monkeypatch):
    service, _ = _service(tmp_path, monkeypatch)
    result = service.analyze_impact(
        {
            "request_id": "REQ-KNOWLEDGE-GAP-1",
            "device_id": "DUT-KNOWLEDGE-GAP",
            "usage_context": {},
            "requested_topics": ["percentage_used"],
            "confirmed_facts": [],
        }
    )
    assert result["status"] == "INSUFFICIENT_KNOWLEDGE"
    assert result["impacts"] == []
