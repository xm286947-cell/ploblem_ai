from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from quality_knowledge.major_cases.context import MajorProblemContextProjection
from quality_knowledge.major_cases.repository import MajorKnowledgeRepository
from quality_knowledge.major_cases.service import MajorCaseService
from quality_knowledge.major_cases.sources import BusinessSourceGateway
from quality_knowledge.web.major_case_pages import create_major_case_router


class _FixtureGateway(BusinessSourceGateway):
    def __init__(self, evidence: dict[str, dict]):
        self.evidence = evidence

    def fetch_summaries(self, group_code: str, standard_itrs):
        return {value: [] for value in standard_itrs}

    def fetch_evidence(self, group_code: str, source_type: str, record_id: str):
        return self.evidence.get(record_id)


def _service(tmp_path: Path, evidence: dict[str, dict]):
    repo = MajorKnowledgeRepository(tmp_path / "knowledge.sqlite3", tmp_path / "attachments")
    service = MajorCaseService(repo, _FixtureGateway(evidence))
    case = service.create_case("重大问题", "G1")
    event = repo.upsert_event(case["case_id"], standard_itr="ITR-001", internal_event_key="ITR-001")
    repo.add_source_link(
        case["case_id"], event["event_id"],
        {"source_type": "ITR_CS", "source_system": "BUSINESS_DB", "record_id": "MAT-001", "group_code": "G1"},
        standard_itr="ITR-001", role="CURRENT_EVENT", status="LINKED",
    )
    return repo, service


def _client(tmp_path: Path, evidence: dict[str, dict]) -> TestClient:
    repo, service = _service(tmp_path, evidence)
    app = FastAPI()
    app.include_router(create_major_case_router(repo, service, None, tmp_path, tmp_path / "runs"))
    return TestClient(app)


def test_c01_c03_contract_shape_and_forbidden_organization_field(tmp_path: Path):
    _, service = _service(tmp_path, {"MAT-001": {"raw": {
        "问题信息_IPMT": "控制产品IPMT", "问题信息_SPDT": "PLC SPDT",
        "问题信息_产品型号": "PLC", "问题信息_客户名称": "客户A", "问题信息_客户行业": "锂电",
    }}})
    payload = MajorProblemContextProjection(service).project("ITR-001")
    assert payload is not None
    assert set(payload) == {
        "contract_version", "problem_id", "product", "customer", "industry",
        "organization", "relation_status", "source_refs",
    }
    assert payload["contract_version"] == "major-problem-context/v1"
    assert payload["product"] == {"product_code": None, "product_name": "PLC"}
    assert payload["customer"] == {"customer_id": None, "customer_name": "客户A"}
    assert payload["industry"] == {"industry_code": None, "industry_name": "锂电"}
    assert payload["organization"] == {
        "ipmt": {"code": None, "name": "控制产品IPMT"},
        "spdt": {"code": None, "name": "PLC SPDT"},
    }
    assert set(payload["organization"]) == {"ipmt", "spdt"}


def test_c04_c05_null_ids_and_partial_facts_are_stable(tmp_path: Path):
    client = _client(tmp_path, {"MAT-001": {"raw": {"问题信息_客户行业": "锂电"}}})
    response = client.get("/api/v2/major-problems/ITR-001/context")
    assert response.status_code == 200
    payload = response.json()
    assert payload["customer"] == {"customer_id": None, "customer_name": None}
    assert payload["industry"] == {"industry_code": None, "industry_name": "锂电"}
    assert payload["product"] == {"product_code": None, "product_name": None}
    assert payload["organization"]["ipmt"] == {"code": None, "name": None}
    assert payload["organization"]["spdt"] == {"code": None, "name": None}


def test_c06_unknown_problem_uses_existing_http_not_found_shape(tmp_path: Path):
    client = _client(tmp_path, {})
    response = client.get("/api/v2/major-problems/UNKNOWN/context")
    assert response.status_code == 404
    assert response.json()["detail"] == "major problem not found"


def test_c07_existing_major_case_api_remains_available(tmp_path: Path):
    client = _client(tmp_path, {})
    response = client.get("/api/knowledge/major-cases")
    assert response.status_code == 200
    assert response.json()["total"] == 1


def test_c08_projection_has_no_database_or_quality_scenario_boundary_access():
    source = Path(__file__).parents[1] / "quality_knowledge/major_cases/context.py"
    text = source.read_text(encoding="utf-8")
    assert "sqlite3" not in text
    assert ".connect(" not in text
    assert "MajorKnowledgeRepository" not in text
    assert "scenario" not in text.lower()
