from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from quality_knowledge.web.runtime_observation_api import (
    create_runtime_observation_router,
)
from runtime.contracts import (
    RuntimeObservation,
    RuntimeObservationAvailability,
    RuntimeObservationQuality,
)
from runtime.observation import RuntimeObservationError, RuntimeObservationService
from runtime.store.observation import RuntimeObservationRepository
from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.web.p0_app import create_p0_app


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


def api_client(tmp_path):
    service = RuntimeObservationService(
        RuntimeObservationRepository(tmp_path / "observations.db")
    )
    app = FastAPI()
    app.include_router(create_runtime_observation_router(service))
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
    payload = valid_observation(**{field: None})

    with pytest.raises(RuntimeObservationError) as error:
        service.create(RuntimeObservation.model_validate(payload))

    assert error.value.code == "RUNTIME_OBSERVATION_FAIL_CLOSED"
    assert expected_detail in error.value.details


@pytest.mark.parametrize(
    "availability",
    ["NOT_AVAILABLE", "NOT_SUPPORTED", "STALE", "INVALID"],
)
def test_non_available_states_are_stored_but_never_formally_consumed(tmp_path, availability):
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
            quality_status="UNKNOWN" if availability != "INVALID" else "INVALID",
            availability_status=availability,
            missing_reason=f"{availability}_READING",
        )
    )

    stored = service.create(observation)

    assert stored.is_formally_consumable is False
    assert service.formal_consumption(stored)["allowed"] is False


def test_forbidden_datasheet_or_mock_source_is_rejected(tmp_path):
    service = RuntimeObservationService(
        RuntimeObservationRepository(tmp_path / "observations.db")
    )

    with pytest.raises(RuntimeObservationError) as error:
        service.create(
            RuntimeObservation.model_validate(
                valid_observation(source_command_or_interface="mock runtime value")
            )
        )

    assert "RUNTIME_SOURCE_NOT_AUTHORIZED" in error.value.details


@pytest.mark.parametrize(
    "override",
    [{"unit": None}, {"unit": "   "}, {"normalized_value": None}],
)
def test_available_observation_requires_unit_and_normalized_value(tmp_path, override):
    service = RuntimeObservationService(
        RuntimeObservationRepository(tmp_path / "observations.db")
    )
    with pytest.raises(RuntimeObservationError) as error:
        service.create(RuntimeObservation.model_validate(valid_observation(**override)))
    assert error.value.code == "RUNTIME_OBSERVATION_FAIL_CLOSED"


def test_unknown_metric_must_be_explicitly_not_supported(tmp_path):
    service = RuntimeObservationService(
        RuntimeObservationRepository(tmp_path / "observations.db")
    )
    observation = RuntimeObservation.model_validate(
        valid_observation(
            metric_name="future_vendor_metric",
            normalized_value=None,
            capture_time=None,
            source_command_or_interface=None,
            evidence_ref=None,
            quality_status="UNKNOWN",
            availability_status="NOT_SUPPORTED",
            missing_reason="METRIC_NOT_SUPPORTED_BY_ADAPTER",
        )
    )
    assert service.create(observation).availability_status is RuntimeObservationAvailability.NOT_SUPPORTED


def test_api_contract_create_get_and_list(tmp_path):
    client = api_client(tmp_path)
    response = client.post(
        "/storage/runtime-observations",
        json=RuntimeObservation.model_validate(valid_observation()).model_dump(mode="json"),
    )
    assert response.status_code == 201, response.text
    assert response.json()["formal_consumption"]["allowed"] is True

    fetched = client.get("/storage/runtime-observations/obs-001")
    assert fetched.status_code == 200
    assert fetched.json()["observation_id"] == "obs-001"

    listed = client.get("/storage/devices/nvme-001/runtime-observations")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1


def test_api_contract_rejects_missing_evidence(tmp_path):
    client = api_client(tmp_path)
    response = client.post(
        "/storage/runtime-observations",
        json=RuntimeObservation.model_validate(
            valid_observation(evidence_ref=None)
        ).model_dump(mode="json"),
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "RUNTIME_OBSERVATION_FAIL_CLOSED"


def test_existing_p0_app_exposes_runtime_observation_contract(tmp_path):
    project_root = Path(__file__).resolve().parents[1]
    db_path = tmp_path / "p0.db"
    P0Initializer(
        manifest_path=project_root / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=project_root / "quality_knowledge/config/plc_fields.yaml",
    ).initialize(db_path)
    client = TestClient(create_p0_app(db_path, stage_runner=object()))

    response = client.post(
        "/storage/runtime-observations",
        json=RuntimeObservation.model_validate(valid_observation()).model_dump(mode="json"),
    )

    assert response.status_code == 201, response.text
    assert client.get("/storage/devices/nvme-001/runtime-observations").json()["total"] == 1
