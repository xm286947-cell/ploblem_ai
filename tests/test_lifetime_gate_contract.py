from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.contracts import RuntimeObservation
from storage_life import (
    ConfirmedFact, FormalKnowledgeReference, LifetimeAssessmentRequest,
    LifetimeEngine, LifetimeAssessmentStatus, FormulaRegistry, create_lifetime_router,
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
    assert result.formula_id == "EMMC_PRE_EOL_V1"


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


def test_exact_frozen_formula_ids_and_margin_contract():
    required = {
        "SSD_TBW_CONSUMPTION_V1", "SSD_DWPD_OBSERVED_V1", "NVME_DATA_UNITS_WRITTEN_V1",
        "NVME_PERCENTAGE_USED_INTERPRETATION_V1", "EMMC_DEVICE_LIFE_TIME_A_V1",
        "EMMC_DEVICE_LIFE_TIME_B_V1", "EMMC_PRE_EOL_V1", "NAND_PE_MARGIN_V1",
        "NAND_ERASE_COUNT_MARGIN_V1", "GENERIC_WAF_V1", "GENERIC_ENDURANCE_MARGIN_V1",
    }
    assert required <= set(FormulaRegistry.SPECS)
    request = LifetimeAssessmentRequest(device_id="d1", confirmed_facts=[
        ConfirmedFact(fact_id="r", metric_name="rated_pe_cycles", value=100, unit="cycles", evidence_refs=["r-e"]),
        ConfirmedFact(fact_id="o", metric_name="erase_count", value=120, unit="cycles", evidence_refs=["o-e"]),
    ])
    result = LifetimeEngine().assess(request, "nand.erase_count_margin")
    assert result.result == -20
    assert result.boundary_checks == ["BELOW_ZERO_MARGIN"]
    assert result.result_kind.value == "NUMERIC"


def test_fact_evidence_and_unit_normalization_fail_closed():
    missing_evidence = LifetimeEngine().assess(LifetimeAssessmentRequest(device_id="d1", confirmed_facts=[ConfirmedFact(fact_id="r", metric_name="rated_tbw_bytes", value=1, unit="TB")], runtime_observations=[obs("host_written_bytes", 1, "TB")]), "ssd.tbw")
    assert missing_evidence.status is LifetimeAssessmentStatus.INVALID_INPUT
    normalized = LifetimeEngine().assess(LifetimeAssessmentRequest(device_id="d1", confirmed_facts=[ConfirmedFact(fact_id="r", metric_name="rated_tbw_bytes", value=2, unit="TB", evidence_refs=["r-e"])], runtime_observations=[obs("host_written_bytes", 1, "TB")]), "ssd.tbw")
    assert normalized.inputs["rated_tbw_bytes"] == 2 * 1000**4
    assert normalized.replay_trace["inputs"]["rated_tbw_bytes"]["normalized_unit"] == "bytes"
    incompatible = LifetimeEngine().assess(LifetimeAssessmentRequest(device_id="d1", confirmed_facts=[ConfirmedFact(fact_id="r", metric_name="rated_tbw_bytes", value=2, unit="cycles", evidence_refs=["r-e"])], runtime_observations=[obs("host_written_bytes", 1, "TB")]), "ssd.tbw")
    assert incompatible.status is LifetimeAssessmentStatus.INVALID_INPUT


def test_generic_endurance_margin_is_registered_and_traceable():
    result = LifetimeEngine().assess(LifetimeAssessmentRequest(device_id="d1", confirmed_facts=[
        ConfirmedFact(fact_id="r", metric_name="rated_endurance_cycles", value=1000, unit="cycles", evidence_refs=["r-e"]),
        ConfirmedFact(fact_id="o", metric_name="observed_endurance_cycles", value=1200, unit="cycles", evidence_refs=["o-e"]),
    ]), "generic.endurance_margin")
    assert result.formula_id == "GENERIC_ENDURANCE_MARGIN_V1"
    assert result.result == -200
    assert "BELOW_ZERO_MARGIN" in result.boundary_checks
