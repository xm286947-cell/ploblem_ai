from datetime import datetime, timezone

import pytest

from runtime.contracts import RuntimeObservation
from products.storage_rc1.storage_life.lifetime_engine import (
    ConfirmedFact,
    FormalKnowledgeReference,
    LifetimeAssessmentRequest,
    LifetimeAssessmentStatus,
    LifetimeAssumption,
    LifetimeEngine,
)


def fact(name, value, unit=None, evidence="fact-evidence"):
    unit = unit or ("bytes" if name.endswith("bytes") else None)
    return ConfirmedFact(
        fact_id=f"fact-{name}", metric_name=name, value=value, unit=unit,
        evidence_refs=[evidence] if evidence else [],
    )


def observation(name, value, unit="bytes", evidence="runtime-evidence"):
    return RuntimeObservation(
        observation_id=f"obs-{name}", device_id="dev-1", device_type="NVME",
        metric_name=name, raw_value=value, normalized_value=value, unit=unit,
        capture_time=datetime(2026, 9, 26, tzinfo=timezone.utc),
        source_command_or_interface="nvme smart-log /dev/nvme0",
        raw_output_ref=f"raw://{name}", evidence_ref=evidence,
        quality_status="VALID", availability_status="AVAILABLE",
    )


def knowledge(scope="NVMe protocol semantics", params=None):
    return FormalKnowledgeReference(
        knowledge_id="K-NVME-001", release_version="1.0", semantic_scope=scope,
        evidence_refs=["knowledge-evidence"], parameters=params or {},
    )


def test_ssd_tbw_is_deterministic_and_traceable():
    request = LifetimeAssessmentRequest(
        device_id="dev-1",
        confirmed_facts=[fact("rated_tbw_bytes", 1_000_000)],
        runtime_observations=[observation("host_written_bytes", 250_000)],
    )
    result = LifetimeEngine().assess(request, "ssd.tbw")
    assert result.status is LifetimeAssessmentStatus.CALCULATED
    assert result.result == {"consumed_ratio": 0.25, "remaining_bytes": 750_000}
    assert result.formula_id == "SSD_TBW_CONSUMPTION_V1"
    assert result.evidence_refs == ["fact-evidence", "runtime-evidence"]


def test_dwpd_requires_explicit_time_window_assumption():
    request = LifetimeAssessmentRequest(
        device_id="dev-1",
        confirmed_facts=[fact("capacity_bytes", 100)],
        runtime_observations=[observation("host_written_bytes", 100)],
    )
    result = LifetimeEngine().assess(request, "ssd.dwpd")
    assert result.status is LifetimeAssessmentStatus.INSUFFICIENT_DATA
    assert "time_window_days" in result.missing_inputs

    request.assumptions.append(LifetimeAssumption(name="time_window_days", value=10, unit="days"))
    result = LifetimeEngine().assess(request, "ssd.dwpd")
    assert result.status is LifetimeAssessmentStatus.CALCULATED
    assert result.result == 0.1


@pytest.mark.parametrize("metric", ["nvme.data_units_written", "nvme.percentage_used", "emmc.life_time"])
def test_protocol_semantics_fail_closed_without_formal_knowledge(metric):
    observations = {
        "nvme.data_units_written": [observation("data_units_written", 42)],
        "nvme.percentage_used": [observation("percentage_used", 7, unit="%")],
        "emmc.life_time": [
            observation("device_life_time_a", "05", unit="tier"),
            observation("device_life_time_b", "06", unit="tier"),
            observation("pre_eol_info", "01", unit="tier"),
        ],
    }[metric]
    result = LifetimeEngine().assess(
        LifetimeAssessmentRequest(device_id="dev-1", runtime_observations=observations), metric
    )
    assert result.status is LifetimeAssessmentStatus.INSUFFICIENT_DATA
    assert any("FORMAL_KNOWLEDGE" in item for item in result.missing_inputs)


def test_nvme_percentage_used_does_not_convert_protocol_units():
    result = LifetimeEngine().assess(
        LifetimeAssessmentRequest(
            device_id="dev-1",
            runtime_observations=[observation("percentage_used", 7, unit="%")],
            formal_knowledge=[knowledge()],
        ),
        "nvme.percentage_used",
    )
    assert result.result == 7
    assert result.unit == "%"


def test_emmc_tier_interpretation_comes_from_formal_knowledge_parameters():
    request = LifetimeAssessmentRequest(
        device_id="dev-1",
        runtime_observations=[
            observation("device_life_time_a", "05", unit="tier"),
            observation("device_life_time_b", "06", unit="tier"),
            observation("pre_eol_info", "01", unit="tier"),
        ],
        formal_knowledge=[knowledge("eMMC EXT_CSD semantics", {
            "life_time_a_map": {"05": "60-70%"},
            "life_time_b_map": {"06": "70-80%"},
            "pre_eol_map": {"01": "NORMAL"},
        })],
    )
    result = LifetimeEngine().assess(request, "emmc.life_time")
    assert result.status is LifetimeAssessmentStatus.CALCULATED
    assert result.result["life_time_a"] == "60-70%"
    assert "knowledge-evidence" in result.evidence_refs


def test_nand_pe_margin_and_wear_distribution_are_deterministic():
    request = LifetimeAssessmentRequest(
        device_id="dev-1",
        confirmed_facts=[fact("rated_pe_cycles", 3000, "cycles")],
        runtime_observations=[
            observation("erase_count", 1200, "cycles"),
            observation("wear_distribution", [1000, 1200, 1400], "cycles"),
        ],
    )
    engine = LifetimeEngine()
    margin = engine.assess(request, "nand.pe_margin")
    distribution = engine.assess(request, "nand.wear_distribution")
    assert margin.result == 1800
    assert distribution.result == {"min": 1000, "max": 1400, "mean": 1200, "spread": 400}


def test_waf_rejects_zero_host_written_bytes():
    request = LifetimeAssessmentRequest(
        device_id="dev-1",
        confirmed_facts=[fact("media_written_bytes", 100), fact("host_written_bytes", 0)],
    )
    result = LifetimeEngine().assess(request, "generic.waf")
    assert result.status is LifetimeAssessmentStatus.INVALID_INPUT


def test_stale_observation_is_not_used_as_current_value():
    stale = observation("host_written_bytes", 100)
    stale.availability_status = "STALE"
    result = LifetimeEngine().assess(
        LifetimeAssessmentRequest(
            device_id="dev-1",
            confirmed_facts=[fact("rated_tbw_bytes", 1000)],
            runtime_observations=[stale],
        ),
        "ssd.tbw",
    )
    assert result.status is LifetimeAssessmentStatus.INSUFFICIENT_DATA
