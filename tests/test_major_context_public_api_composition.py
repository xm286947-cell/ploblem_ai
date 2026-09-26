from __future__ import annotations

from fastapi.testclient import TestClient

from quality_knowledge.p04.adapter import ProviderSnapshot
from quality_knowledge.p04.contracts import P04State
from quality_knowledge.p04.major_context import (
    CallableMajorProblemContextClient,
    IntegratedP04Provider,
)
from quality_knowledge.major_cases.context import (
    CONTRACT_VERSION,
    RepositoryMajorProblemContextProvider,
)
from quality_knowledge.major_cases.repository import MajorKnowledgeRepository
from quality_knowledge.web.p0_app import create_p0_app


def test_unified_app_composes_public_major_context_route(tmp_path):
    repository = MajorKnowledgeRepository(
        tmp_path / "major.db", tmp_path / "attachments"
    )
    case = repository.create_case("Problem", "GROUP-1")
    event = repository.upsert_event(
        case["case_id"], standard_itr="ITR-1", internal_event_key="E-1"
    )
    repository.add_source_link(
        case["case_id"],
        event["event_id"],
        {
            "source_system": "problem-system",
            "source_type": "problem",
            "record_id": "SOURCE-1",
            "product": {"product_code": "P-1", "product_name": "Product"},
            "customer": {"customer_id": "C-1", "customer_name": "Customer"},
            "industry": {"industry_code": "I-1", "industry_name": "Industry"},
            "organization": {
                "ipmt": {"code": "IPMT-1", "name": "IPMT"},
                "spdt": {"code": "SPDT-1", "name": "SPDT"},
            },
        },
        standard_itr="ITR-1",
        role="CURRENT_EVENT",
        status="LINKED",
    )

    app = create_p0_app(
        tmp_path / "host.db",
        enabled_domains={"HARDWARE_CASE"},
        major_context_provider=RepositoryMajorProblemContextProvider(repository),
    )
    client = TestClient(app)
    response = client.get("/api/v2/major-problems/ITR-1/context")

    assert response.status_code == 200
    body = response.json()
    assert body["contract_version"] == CONTRACT_VERSION
    assert body["problem_id"] == "ITR-1"
    assert body["source_refs"] == ["ITR-1"]
    assert body["product"]["product_code"] == "P-1"
    assert body["customer"]["customer_id"] == "C-1"
    assert body["industry"]["industry_code"] == "I-1"
    assert body["organization"]["ipmt"]["code"] == "IPMT-1"
    assert body["organization"]["spdt"]["code"] == "SPDT-1"
    assert "sbdt" not in body

    assert client.get("/api/v2/major-problems/UNKNOWN/context").status_code == 404

    class QualityScenarioSource:
        def snapshot(self):
            return ProviderSnapshot(
                scenarios=(
                    {
                        "scenario_id": "QS-1",
                        "status": "PUBLISHED",
                        "source_problem_ids": ["ITR-1"],
                    },
                ),
                result_revision="live-v1",
                state=P04State.NORMAL,
            )

    integrated = IntegratedP04Provider(
        QualityScenarioSource(),
        CallableMajorProblemContextClient(
            lambda problem_id: client.get(
                f"/api/v2/major-problems/{problem_id}/context"
            )
        ),
    )
    snapshot = integrated.snapshot()
    assert snapshot.state == P04State.NORMAL
    assert snapshot.scenarios[0]["product"] == "Product"


def test_business_problem_can_exist_with_partial_context_and_null_ids(tmp_path):
    repository = MajorKnowledgeRepository(
        tmp_path / "major.db", tmp_path / "attachments"
    )
    case = repository.create_case("Problem", "GROUP-1")
    event = repository.upsert_event(
        case["case_id"], standard_itr="ITR-001", internal_event_key="E-1"
    )
    repository.add_source_link(
        case["case_id"],
        event["event_id"],
        {"source_system": "problem-system", "source_type": "problem", "record_id": "SOURCE-1"},
        standard_itr="ITR-001",
        role="CURRENT_EVENT",
        status="LINKED",
    )
    app = create_p0_app(
        tmp_path / "host.db",
        enabled_domains={"HARDWARE_CASE"},
        major_context_provider=RepositoryMajorProblemContextProvider(repository),
    )
    response = TestClient(app).get("/api/v2/major-problems/ITR-001/context")
    assert response.status_code == 200
    body = response.json()
    assert body["problem_id"] == "ITR-001"
    assert body["relation_status"] == "OBSERVED_IN_PROBLEM_EVIDENCE"
    assert body["product"] == {"product_code": None, "product_name": None}
    assert body["customer"] == {"customer_id": None, "customer_name": None}
    assert body["industry"] == {"industry_code": None, "industry_name": None}
    assert body["organization"] == {
        "ipmt": {"code": None, "name": None},
        "spdt": {"code": None, "name": None},
    }
    assert body["source_refs"] == ["ITR-001"]

    assert TestClient(app).get(
        f"/api/v2/major-problems/{case['case_id']}/context"
    ).status_code == 404
    assert TestClient(app).get(
        "/api/v2/major-problems/SOURCE-1/context"
    ).status_code == 404
