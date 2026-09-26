from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from products.storage_rc1.storage_life.engineering_insight import (
    StorageEngineeringInsightService,
    create_engineering_insight_router,
)
from products.storage_rc1.storage_life import core
from runtime.contracts import (
    RuntimeObservation,
    RuntimeObservationAvailability,
    RuntimeObservationQuality,
)
from runtime.observation import RuntimeObservationError, RuntimeObservationService
from runtime.store.observation import RuntimeObservationRepository


def valid_observation(**overrides):
    payload = {
        "observation_id": "obs-001",
        "device_id": "nvme-001",
        "device_type": "NVME",
        "metric_name": "percentage_used",
        "raw_value": "7",
        "normalized_value": 7,
        "unit": "%",
        "capture_time": datetime(2026, 9, 26, 1, 2, 3, tzinfo=timezone.utc),
        "source_command_or_interface": "nvme smart-log /dev/nvme0",
        "raw_output_ref": "raw://obs-001",
        "evidence_ref": "evidence://obs-001",
        "collector": "nvme-smart",
        "environment": {"host": "test"},
        "quality_status": "VALID",
        "availability_status": "AVAILABLE",
        "schema_version": "1.0",
    }
    payload.update(overrides)
    return payload


def api_client(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB", tmp_path / "observations.db")
    monkeypatch.setattr(core, "DATA", tmp_path)
    service = StorageEngineeringInsightService()
    app = FastAPI()
    app.include_router(create_engineering_insight_router(service))
    return TestClient(app)


def test_model_freezes_fields_and_enums():
    observation = RuntimeObservation.model_validate(valid_observation())
    assert observation.availability_status is RuntimeObservationAvailability.AVAILABLE
    assert observation.quality_status is RuntimeObservationQuality.VALID
    assert observation.is_formally_consumable is True

    with pytest.raises(ValidationError):
        RuntimeObservation.model_validate({**valid_observation(), "unexpected": True})


def test_repository_create_get_and_list(tmp_path):
    repository = RuntimeObservationRepository(tmp_path / "observations.db")
    observation = RuntimeObservation.model_validate(valid_observation())
    repository.create(observation)
    assert repository.get("obs-001") == observation
    assert repository.list(device_id="nvme-001")[0] == observation


@pytest.mark.parametrize(
    ("field", "expected_detail"),
    [
        ("capture_time", "CAPTURE_TIME_REQUIRED"),
        ("source_command_or_interface", "SOURCE_COMMAND_OR_INTERFACE_REQUIRED"),
        ("evidence_ref", "EVIDENCE_REF_REQUIRED"),
    ],
)
def test_available_observation_is_fail_closed_when_required_evidence_is_missing(
    tmp_path, field, expected_detail
):
    service = RuntimeObservationService(
        RuntimeObservationRepository(tmp_path / "observations.db")
    )
    with pytest.raises(RuntimeObservationError) as error:
        service.create(
            RuntimeObservation.model_validate(valid_observation(**{field: None}))
        )
    assert error.value.code == "RUNTIME_OBSERVATION_FAIL_CLOSED"
    assert expected_detail in error.value.details


@pytest.mark.parametrize("availability", ["NOT_AVAILABLE", "NOT_SUPPORTED", "STALE", "INVALID"])
def test_non_available_states_never_become_current_values(tmp_path, availability):
    service = RuntimeObservationService(
        RuntimeObservationRepository(tmp_path / "observations.db")
    )
    observation = RuntimeObservation.model_validate(
        valid_observation(
            observation_id=f"obs-{availability.lower()}",
            normalized_value=None,
            capture_time=None,
            source_command_or_interface=None,
            evidence_ref=None,
            quality_status="INVALID" if availability == "INVALID" else "UNKNOWN",
            availability_status=availability,
            missing_reason=f"{availability}_READING",
        )
    )
    stored = service.create(observation)
    assert stored.is_formally_consumable is False
    assert service.formal_consumption(stored)["allowed"] is False


def test_api_contract_create_get_and_list(tmp_path, monkeypatch):
    client = api_client(tmp_path, monkeypatch)
    response = client.post(
        "/storage/runtime-observations",
        json=RuntimeObservation.model_validate(valid_observation()).model_dump(mode="json"),
    )
    assert response.status_code == 201, response.text
    assert response.json()["formal_consumption"]["allowed"] is True
    assert client.get("/storage/runtime-observations/obs-001").status_code == 200
    assert client.get("/storage/devices/nvme-001/runtime-observations").json()["total"] == 1
