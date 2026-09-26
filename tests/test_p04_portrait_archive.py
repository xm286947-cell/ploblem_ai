from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from quality_knowledge.p04.portrait import (
    ARCHIVE_CONTRACT,
    PORTRAIT_CONTRACT,
    FixturePortraitProvider,
    PortraitArchiveRepository,
    PortraitService,
)
from quality_knowledge.p04.portrait_api import create_portrait_router


def test_portrait_query_and_archive_contracts(tmp_path) -> None:
    provider = FixturePortraitProvider()
    service = PortraitService(provider, PortraitArchiveRepository(tmp_path / "portrait.db"))

    projection = service.query({"customer_ref": "CUSTOMER-A"})
    assert projection.contract_version == PORTRAIT_CONTRACT
    assert projection.input_count == 2
    assert projection.result["product_count"] == 2
    job_id = service.create_job({"customer_ref": "CUSTOMER-A"})
    archive = service.archive_job(job_id)
    assert archive["contract_version"] == ARCHIVE_CONTRACT
    detail = service.repository.get_archive(archive["archive_id"])
    assert detail.input_count == 2
    assert detail.input_snapshot
    assert detail.result_snapshot["scenario_ids"] == ["QS-FIX-001", "QS-FIX-002"]
    assert detail.input_hash


def test_duplicate_archive_is_fail_closed(tmp_path) -> None:
    service = PortraitService(FixturePortraitProvider(), PortraitArchiveRepository(tmp_path / "portrait.db"))
    job_id = service.create_job({"customer_ref": "CUSTOMER-A"})
    service.archive_job(job_id)
    try:
        service.archive_job(job_id)
    except ValueError as error:
        assert str(error) == "ALREADY_ARCHIVED"
    else:
        raise AssertionError("duplicate archive must fail closed")


def test_archive_detail_is_immutable_when_live_provider_changes(tmp_path) -> None:
    provider = FixturePortraitProvider()
    service = PortraitService(provider, PortraitArchiveRepository(tmp_path / "portrait.db"))
    job_id = service.create_job({"customer_ref": "CUSTOMER-A"})
    archive_id = service.archive_job(job_id)["archive_id"]
    before = service.repository.get_archive(archive_id).model_dump(mode="json")

    provider.replace([{
        "customer_ref": "CUSTOMER-A",
        "product_ref": "PRODUCT-C",
        "industry_ref": "INDUSTRY-B",
        "quality_focus": "SAFETY",
        "scenario_id": "QS-LIVE-CHANGED",
        "source_problem_ids": ["PROBLEM-CHANGED"],
    }], revision="live-v2")
    after = service.repository.get_archive(archive_id).model_dump(mode="json")
    assert after == before
    assert after["result_snapshot"]["scenario_ids"] == ["QS-FIX-001", "QS-FIX-002"]


def test_portrait_archive_api_contracts(tmp_path) -> None:
    provider = FixturePortraitProvider()
    service = PortraitService(provider, PortraitArchiveRepository(tmp_path / "portrait.db"))
    app = FastAPI()
    app.include_router(create_portrait_router(service))
    client = TestClient(app)

    query = client.post(
        "/api/v2/quality-scenario-insights/v1/customer-quality-portrait/v1/query",
        json={"customer_ref": "CUSTOMER-A"},
    )
    assert query.status_code == 200
    assert query.json()["contract_version"] == PORTRAIT_CONTRACT

    job = client.post(
        "/api/v2/quality-scenario-insights/v1/customer-quality-portrait-archive/v1/jobs",
        json={"filters": {"customer_ref": "CUSTOMER-A"}},
    )
    job_id = job.json()["job_id"]
    archived = client.post(
        f"/api/v2/quality-scenario-insights/v1/customer-quality-portrait-archive/v1/jobs/{job_id}/archive"
    )
    assert archived.status_code == 200
    archive_id = archived.json()["archive_id"]
    duplicate = client.post(
        f"/api/v2/quality-scenario-insights/v1/customer-quality-portrait-archive/v1/jobs/{job_id}/archive"
    )
    assert duplicate.status_code == 409
    listing = client.get("/api/v2/quality-scenario-insights/v1/customer-quality-portrait-archive/v1")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    detail = client.get(
        f"/api/v2/quality-scenario-insights/v1/customer-quality-portrait-archive/v1/{archive_id}"
    )
    assert detail.status_code == 200
    assert detail.json()["contract_version"] == ARCHIVE_CONTRACT
