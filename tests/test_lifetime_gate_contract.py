from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.contracts import RuntimeObservation
from storage_life import (
    ConfirmedFact, FormalKnowledgeReference, LifetimeAssessmentRequest,
    LifetimeEngine, LifetimeAssessmentStatus, create_lifetime_router,
)


def obs(metric, value, unit):
    return RuntimeObservation(
        observation_id=f"obs-{metric}", device_id="d1", device_type="NVME",
        metric_name=metric, raw_value=value, normalized_value=value, unit=unit,
        capture_time=datetime(2026, 9, 26, tzinfo=timezone.utc),
        source_command_or_interface="formal-collector", raw_output_ref=f"raw://{metric}",
        evidence_ref=f"evidence://{metric}", quality_status="VALID", availability_status="AVAILABLE",
    )


def kn(params):
    return FormalKnowledgeReference(knowledge_id="K-1", release_version="1", semantic_scope="protocol", evidence_refs=["knowledge-evidence"], parameters=params)


def test_nvme_factor_and_replay_trace_are_explicit():
    result = LifetimeEngine().assess(LifetimeAssessmentRequest(device_id="d1", runtime_observations=[obs("data_units_written", 7, "data_units")], formal_knowledge=[kn({"bytes_per_data_unit": 512000})]), "nvme.data_units_written")
    assert result.status is LifetimeAssessmentStatus.CALCULATED
    assert result.result == 3584000
    assert result.replay_trace["inputs"]["bytes_per_data_unit"]["source_type"] == "FORMAL_KNOWLEDGE_PARAMETER"


def test_pre_eol_is_separate_from_life_time_a_and_b():
    request = LifetimeAssessmentRequest(device_id="d1", runtime_observations=[obs("pre_eol_info", "01", "tier")], formal_knowledge=[kn({"pre_eol_map": {"01": "NORMAL"}})])
    result = LifetimeEngine().assess(request, "emmc.pre_eol_info")
    assert result.result["interpretation"] == "NORMAL"
    assert result.formula_id == "emmc.pre_eol_info"


def test_negative_pe_margin_is_preserved():
    result = LifetimeEngine().assess(LifetimeAssessmentRequest(device_id="d1", confirmed_facts=[ConfirmedFact(fact_id="r", metric_name="rated_pe_cycles", value=100, unit="cycles", evidence_refs=["r-e"]), ConfirmedFact(fact_id="o", metric_name="erase_count", value=120, unit="cycles", evidence_refs=["o-e"])]), "nand.pe_margin")
    assert result.result == -20


def test_four_formal_lifetime_apis_are_present():
    app = FastAPI(); app.include_router(create_lifetime_router()); client = TestClient(app)
    assert client.get("/storage/lifetime/formulas").status_code == 200
    assert client.get("/storage/lifetime/formulas/nvme.data_units_written").status_code == 200
    body = {"metric": "nvme.percentage_used", "device_id": "d1", "runtime_observations": [obs("percentage_used", 3, "%").model_dump(mode="json")], "formal_knowledge": [kn({}).model_dump(mode="json")]}
    created = client.post("/storage/lifetime/assess", json=body)
    assert created.status_code == 201
    assert client.get(f"/storage/lifetime/assessments/{created.json()['assessment_id']}").status_code == 200
