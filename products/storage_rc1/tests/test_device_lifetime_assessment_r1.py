from fastapi import FastAPI
from fastapi.testclient import TestClient

from storage_life import core, product_api
from storage_life.engineering_insight import StorageEngineeringInsightService, create_engineering_insight_router
from storage_life.device_lifetime_assessment import (
    DeviceLifetimeAssessmentComposer,
    DeviceLifetimeOverallStatus,
)
from storage_life.lifetime_engine import (
    LifetimeAssessmentResult,
    LifetimeAssessmentStatus,
    ResultKind,
)
from storage_life.software_impact import (
    SoftwareImpactAnalysisResult,
    SoftwareImpactAnalysisStatus,
)


def _metric(metric, status, *, result=None, evidence=None, knowledge=None, missing=None):
    return LifetimeAssessmentResult(
        assessment_id="A-" + metric,
        device_id="dev-1",
        metric=metric,
        status=status,
        formula_id="TEST_FORMULA",
        formula_version="1",
        result_kind=ResultKind.RANGE,
        result=result,
        evidence_refs=evidence or [],
        knowledge_refs=knowledge or [],
        missing_inputs=missing or [],
    )


def _impact(status=SoftwareImpactAnalysisStatus.INSUFFICIENT_KNOWLEDGE):
    return SoftwareImpactAnalysisResult(
        request_id="REQ-1",
        analysis_id="IMP-1",
        device_id="dev-1",
        status=status,
        missing_information=["FORMAL_KNOWLEDGE_RELEASE_WITH_EVIDENCE_REQUIRED"]
        if status is not SoftwareImpactAnalysisStatus.EVIDENCED else [],
    )


def test_device_composer_assessed_nvme_ssd_and_trace():
    metrics = [
        _metric(
            "ssd.tbw",
            LifetimeAssessmentStatus.CALCULATED,
            result={"consumed_ratio": 0.25, "remaining_bytes": 750},
            evidence=["ev-tbw"],
            knowledge=["kr-tbw"],
        ),
        _metric(
            "nvme.percentage_used",
            LifetimeAssessmentStatus.CALCULATED,
            result=25,
            evidence=["ev-smart"],
            knowledge=["kr-nvme"],
        ),
    ]
    result = DeviceLifetimeAssessmentComposer.compose(
        device_id="dev-1",
        device_type="NVMe SSD",
        requested_metrics=["ssd.tbw", "nvme.percentage_used"],
        metric_results=metrics,
        software_impact=_impact(),
    )
    assert result.overall_status is DeviceLifetimeOverallStatus.ASSESSED
    assert result.coverage["assessed_count"] == 2
    assert result.evidence_refs == ["ev-smart", "ev-tbw"]
    assert result.knowledge_refs == ["kr-nvme", "kr-tbw"]
    assert result.decision_boundary == "NO_AUTO_REPLACEMENT_DECISION"
    assert "remaining_years" not in result.model_dump(mode="json")


def test_device_composer_partial_is_fail_closed():
    result = DeviceLifetimeAssessmentComposer.compose(
        device_id="dev-1",
        device_type="NVMe SSD",
        requested_metrics=["ssd.tbw", "nvme.percentage_used"],
        metric_results=[
            _metric("ssd.tbw", LifetimeAssessmentStatus.CALCULATED, result={"consumed_ratio": 0.5}, evidence=["ev"]),
            _metric("nvme.percentage_used", LifetimeAssessmentStatus.INSUFFICIENT_DATA, missing=["percentage_used:RUNTIME_OBSERVATION_REQUIRED"]),
        ],
        software_impact=_impact(),
    )
    assert result.overall_status is DeviceLifetimeOverallStatus.PARTIAL
    assert "nvme.percentage_used" in result.coverage["missing_metrics"]
    assert "PROVIDE_EVIDENCE_FOR:nvme.percentage_used" in result.next_required_actions
    assert "FORMAL_KNOWLEDGE_RELEASE_WITH_EVIDENCE_REQUIRED" in result.missing_information


def test_device_composer_invalid_wins_over_partial_result():
    result = DeviceLifetimeAssessmentComposer.compose(
        device_id="dev-1",
        device_type="eMMC",
        requested_metrics=["emmc.device_life_time_a", "emmc.pre_eol_info"],
        metric_results=[
            _metric("emmc.device_life_time_a", LifetimeAssessmentStatus.INVALID_INPUT),
            _metric("emmc.pre_eol_info", LifetimeAssessmentStatus.INSUFFICIENT_DATA, missing=["FORMAL_KNOWLEDGE_RELEASE_REQUIRED"]),
        ],
        software_impact=_impact(),
    )
    assert result.overall_status is DeviceLifetimeOverallStatus.INVALID
    assert result.decision_boundary == "NO_AUTO_REPLACEMENT_DECISION"


def test_device_lifetime_api_uses_existing_service_and_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DATA", tmp_path)
    monkeypatch.setattr(core, "DB", tmp_path / "r1-api.sqlite3")
    service = StorageEngineeringInsightService()
    monkeypatch.setattr(
        product_api,
        "confirmed_device_facts",
        lambda device_id: {"device": {"id": device_id, "device_type": "NVMe SSD"}, "facts": []},
    )

    def fake_lifetime(payload):
        metric = payload["metric"]
        if metric == "ssd.tbw":
            result = {"consumed_ratio": 0.25, "remaining_bytes": 750}
            evidence = ["ev-tbw"]
            knowledge = ["kr-tbw"]
        else:
            result = 25
            evidence = ["ev-smart"]
            knowledge = ["kr-nvme"]
        return _metric(
            metric,
            LifetimeAssessmentStatus.CALCULATED,
            result=result,
            evidence=evidence,
            knowledge=knowledge,
        ).model_dump(mode="json")

    monkeypatch.setattr(service, "assess_lifetime", fake_lifetime)
    monkeypatch.setattr(
        service,
        "analyze_impact",
        lambda payload: _impact().model_dump(mode="json"),
    )

    app = FastAPI()
    app.include_router(create_engineering_insight_router(service))
    response = TestClient(app).get("/storage/devices/dev-1/lifetime-assessment")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["device_id"] == "dev-1"
    assert body["overall_status"] == "ASSESSED"
    assert body["coverage"]["assessed_count"] == 2
    assert body["decision_boundary"] == "NO_AUTO_REPLACEMENT_DECISION"
    assert "remaining_years" not in body


def test_device_lifetime_api_missing_device_is_404(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DATA", tmp_path)
    monkeypatch.setattr(core, "DB", tmp_path / "r1-api-missing.sqlite3")
    service = StorageEngineeringInsightService()
    monkeypatch.setattr(
        product_api,
        "confirmed_device_facts",
        lambda _device_id: (_ for _ in ()).throw(KeyError("missing")),
    )
    app = FastAPI()
    app.include_router(create_engineering_insight_router(service))
    response = TestClient(app).get("/storage/devices/missing/lifetime-assessment")
    assert response.status_code == 404
