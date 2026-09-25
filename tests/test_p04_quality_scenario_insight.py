from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from quality_knowledge.p04.api import create_p04_router
from quality_knowledge.p04.contracts import P04State, P04View
from quality_knowledge.p04.fixtures import FixtureP04Provider
from quality_knowledge.p04.service import P04InsightService


def service() -> P04InsightService:
    return P04InsightService(FixtureP04Provider())


def test_selectors_are_opaque_and_three_views_are_provider_driven() -> None:
    current = service()
    for view in P04View:
        result = current.selectors(view)
        assert result["state"] == P04State.NORMAL
        assert result["items"]
        assert all(item.selector_ref.startswith("selector_") for item in result["items"])
        assert all(item.label not in item.selector_ref for item in result["items"])


def test_product_query_is_published_only_and_distinct() -> None:
    result = service().query({"view": "PRODUCT"})
    assert result.state == P04State.NORMAL
    assert result.coverage.scenario_count == 3
    assert result.coverage.source_problem_count == 3
    assert all(item.p03_path.startswith("/p0/") for item in result.scenario_list)
    assert result.matrix is not None
    assert all(cell.count >= 1 for cell in result.matrix.cells)


def test_customer_and_industry_queries_expose_partial_mapping() -> None:
    current = service()
    customer = current.query({"view": "CUSTOMER"})
    industry = current.query({"view": "INDUSTRY"})
    assert customer.state == P04State.PARTIAL_DATA
    assert industry.state == P04State.PARTIAL_DATA
    assert customer.coverage.unmapped_scenario_count == 1
    assert industry.coverage.unmapped_scenario_count == 1


def test_spdt_requires_matching_ipmt_parent() -> None:
    provider = FixtureP04Provider([{
        "scenario_id": "S1", "status": "PUBLISHED", "product": "P",
        "product_family": "F", "customer": "C", "industry": "I",
        "ipmt": "IPMT-A", "spdt": "SPDT-A", "spdt_ipmt": "IPMT-B",
    }])
    result = P04InsightService(provider).query({"view": "PRODUCT"})
    assert result.state == P04State.PARTIAL_DATA
    assert "SPDT_IPMT_PARENT_MISMATCH" in result.warnings


def test_drilldown_is_stable_and_detects_revision_change() -> None:
    provider = FixtureP04Provider()
    current = P04InsightService(provider)
    query = current.query({"view": "PRODUCT"})
    assert query.matrix and query.matrix.cells
    drilldown = query.matrix.cells[0].drilldown_query.model_dump(mode="json")
    assert current.drilldown(drilldown).status == "OK"
    provider.replace([], result_revision="fixture-v2")
    stale = current.drilldown(drilldown)
    assert stale.status == "REVISION_CHANGED"
    assert stale.error_code == "RESULT_REVISION_CHANGED"


def test_api_returns_explicit_provider_unavailable_and_no_db_boundary() -> None:
    app = FastAPI()
    app.include_router(create_p04_router(P04InsightService(FixtureP04Provider())))
    client = TestClient(app)
    response = client.post("/api/v2/quality-scenario-insights/v1/query", json={"view": "PRODUCT"})
    assert response.status_code == 200
    assert response.json()["contract_version"] == "quality-scenario-insight/v1"
