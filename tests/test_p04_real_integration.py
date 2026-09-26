from __future__ import annotations

from copy import deepcopy

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from quality_knowledge.p04.adapter import ProviderSnapshot
from quality_knowledge.p04.contracts import P04State
from quality_knowledge.p04.major_context import (
    CONTRACT_VERSION,
    CallableMajorProblemContextClient,
    IntegratedP04Provider,
    IntegratedPortraitProvider,
)
from quality_knowledge.p04.portrait import PortraitArchiveRepository, PortraitService
from quality_knowledge.p04.service import P04InsightService


class LiveQualityScenarioProvider:
    def __init__(self, rows: list[dict], revision: str = "live-v1") -> None:
        self.rows = deepcopy(rows)
        self.revision = revision

    def snapshot(self) -> ProviderSnapshot:
        return ProviderSnapshot(tuple(deepcopy(self.rows)), self.revision, P04State.NORMAL)


def _major_api(contexts: dict[str, dict], *, status: int = 200) -> TestClient:
    app = FastAPI()

    @app.get("/api/v2/major-problems/{problem_id}/context")
    def context(problem_id: str):
        if problem_id not in contexts:
            raise HTTPException(404, "major problem not found")
        if status != 200:
            raise HTTPException(status, "provider failure")
        return contexts[problem_id]

    return TestClient(app)


def _context(problem_id: str, *, null_ids: bool = False) -> dict:
    return {
        "contract_version": CONTRACT_VERSION,
        "problem_id": problem_id,
        "product": {"product_code": None if null_ids else "P-01", "product_name": "Product One"},
        "customer": {"customer_id": None if null_ids else "C-01", "customer_name": "Customer One"},
        "industry": {"industry_code": None if null_ids else "I-01", "industry_name": "Industry One"},
        "organization": {
            "ipmt": {"code": None if null_ids else "IPMT-01", "name": "IPMT One"},
            "spdt": {"code": None if null_ids else "SPDT-01", "name": "SPDT One"},
        },
        "relation_status": "OBSERVED_IN_PROBLEM_EVIDENCE",
        "source_refs": [f"major:{problem_id}:evidence"],
    }


def _rows() -> list[dict]:
    return [{
        "scenario_id": "QS-LIVE-001",
        "status": "PUBLISHED",
        "lifecycle": "RELEASE",
        "business_activity": "CONFIGURATION",
        "quality_focus": "CORRECTNESS",
        "source_problem_ids": ["PROBLEM-001"],
    }, {
        "scenario_id": "QS-DRAFT-001",
        "status": "DRAFT",
        "source_problem_ids": ["PROBLEM-001"],
    }]


def test_real_public_contract_maps_full_context_and_null_ids_without_guessing(tmp_path):
    contexts = {"PROBLEM-001": _context("PROBLEM-001", null_ids=True)}
    major = _major_api(contexts)
    client = CallableMajorProblemContextClient(
        lambda problem_id: major.get(f"/api/v2/major-problems/{problem_id}/context")
    )
    integrated = IntegratedP04Provider(LiveQualityScenarioProvider(_rows()), client)
    snapshot = integrated.snapshot()

    assert snapshot.state == P04State.NORMAL
    assert len(snapshot.scenarios) == 1
    row = snapshot.scenarios[0]
    assert row["product"] == "Product One"
    assert row["customer"] == "Customer One"
    assert row["industry"] == "Industry One"
    assert row["ipmt"] == "IPMT One"
    assert row["spdt"] == "SPDT One"
    assert row["product_code"] is None
    assert row["customer_id"] is None
    assert row["industry_code"] is None
    assert row["ipmt_code"] is None
    assert row["spdt_code"] is None
    assert "sbdt" not in row
    assert row["source_contract_version"] == CONTRACT_VERSION
    assert row["source_refs"] == ["major:PROBLEM-001:evidence"]
    query = P04InsightService(integrated).query({"view": "PRODUCT"})
    assert query.state == P04State.NORMAL
    assert query.total == 1

    portrait = PortraitService(IntegratedPortraitProvider(integrated), PortraitArchiveRepository(tmp_path / "portrait.db"))
    projection = portrait.query({})
    assert projection.state == P04State.NORMAL
    assert projection.input_count == 1
    assert projection.evidence_composition[0]["source_contract_version"] == CONTRACT_VERSION
    job_id = portrait.create_job({})
    archive_id = portrait.archive_job(job_id)["archive_id"]
    detail = portrait.repository.get_archive(archive_id)
    assert detail.input_snapshot[0]["source_contract_version"] == CONTRACT_VERSION
    assert detail.evidence_composition[0]["source_refs"] == ["major:PROBLEM-001:evidence"]

    contexts["PROBLEM-001"] = _context("PROBLEM-001")
    detail_after_live_change = portrait.repository.get_archive(archive_id)
    assert detail_after_live_change.model_dump(mode="json") == detail.model_dump(mode="json")


def test_real_public_contract_keeps_full_context_when_org_codes_differ_from_names():
    contexts = {"PROBLEM-001": _context("PROBLEM-001")}
    major = _major_api(contexts)
    client = CallableMajorProblemContextClient(
        lambda problem_id: major.get(f"/api/v2/major-problems/{problem_id}/context")
    )
    integrated = IntegratedP04Provider(LiveQualityScenarioProvider(_rows()[:1]), client)
    current = P04InsightService(integrated)

    for view in ("PRODUCT", "CUSTOMER", "INDUSTRY"):
        result = current.query({"view": view})
        assert result.state == P04State.NORMAL
        assert result.total == 1
        assert "SPDT_IPMT_PARENT_MISMATCH" not in result.warnings
        assert "UNMAPPED_SCENARIO_PRESENT" not in result.warnings


def test_invalid_org_parent_code_still_fails_closed():
    row = _rows()[:1][0].copy()
    row["spdt_ipmt"] = "IPMT-INVALID"
    contexts = {"PROBLEM-001": _context("PROBLEM-001")}
    major = _major_api(contexts)
    client = CallableMajorProblemContextClient(
        lambda problem_id: major.get(f"/api/v2/major-problems/{problem_id}/context")
    )
    integrated = IntegratedP04Provider(LiveQualityScenarioProvider([row]), client)

    result = P04InsightService(integrated).query({"view": "PRODUCT"})
    assert result.state == P04State.PARTIAL_DATA
    assert result.total == 1
    assert "SPDT_IPMT_PARENT_MISMATCH" in result.warnings


def test_partial_public_context_retains_usable_scenarios():
    row = _rows()[:1][0].copy()
    row["source_problem_ids"] = ["PROBLEM-001", "PROBLEM-MISSING"]
    contexts = {"PROBLEM-001": _context("PROBLEM-001")}
    major = _major_api(contexts)
    client = CallableMajorProblemContextClient(
        lambda problem_id: major.get(f"/api/v2/major-problems/{problem_id}/context")
    )
    integrated = IntegratedP04Provider(LiveQualityScenarioProvider([row]), client)
    current = P04InsightService(integrated)

    for view in ("PRODUCT", "CUSTOMER", "INDUSTRY"):
        result = current.query({"view": view})
        assert result.state == P04State.PARTIAL_DATA
        assert result.total == 1
        assert [item.scenario_id for item in result.scenario_list] == ["QS-LIVE-001"]
        assert "SOURCE_PROBLEM_NOT_FOUND" in result.warnings


def test_unknown_problem_is_no_relation_mapping_and_not_empty():
    major = _major_api({})
    client = CallableMajorProblemContextClient(
        lambda problem_id: major.get(f"/api/v2/major-problems/{problem_id}/context")
    )
    integrated = IntegratedP04Provider(LiveQualityScenarioProvider(_rows()[:1]), client)
    snapshot = integrated.snapshot()
    assert snapshot.state == P04State.NO_RELATION_MAPPING
    assert "SOURCE_PROBLEM_NOT_FOUND" in snapshot.warnings


def test_provider_failure_is_data_unavailable():
    major = _major_api({"PROBLEM-001": _context("PROBLEM-001")}, status=503)
    client = CallableMajorProblemContextClient(
        lambda problem_id: major.get(f"/api/v2/major-problems/{problem_id}/context")
    )
    integrated = IntegratedP04Provider(LiveQualityScenarioProvider(_rows()[:1]), client)
    snapshot = integrated.snapshot()
    assert snapshot.state == P04State.DATA_UNAVAILABLE
    assert "MAJOR_CONTEXT_PROVIDER_UNAVAILABLE" in snapshot.warnings


def test_contract_version_mismatch_fails_closed():
    bad = _context("PROBLEM-001")
    bad["contract_version"] = "major-problem-context/v0"
    major = _major_api({"PROBLEM-001": bad})
    client = CallableMajorProblemContextClient(
        lambda problem_id: major.get(f"/api/v2/major-problems/{problem_id}/context")
    )
    integrated = IntegratedP04Provider(LiveQualityScenarioProvider(_rows()[:1]), client)
    snapshot = integrated.snapshot()
    assert snapshot.state == P04State.ERROR
    assert "CONTRACT_VERSION_MISMATCH" in snapshot.warnings


def test_invalid_relation_status_fails_closed():
    bad = _context("PROBLEM-001")
    bad["relation_status"] = "NO_RELATION_MAPPING"
    major = _major_api({"PROBLEM-001": bad})
    client = CallableMajorProblemContextClient(
        lambda problem_id: major.get(f"/api/v2/major-problems/{problem_id}/context")
    )
    integrated = IntegratedP04Provider(LiveQualityScenarioProvider(_rows()[:1]), client)
    snapshot = integrated.snapshot()
    assert snapshot.state == P04State.ERROR
    assert "CONTRACT_PAYLOAD_INVALID" in snapshot.warnings


def test_missing_required_contract_shape_fails_closed():
    bad = _context("PROBLEM-001")
    del bad["customer"]["customer_name"]
    major = _major_api({"PROBLEM-001": bad})
    client = CallableMajorProblemContextClient(
        lambda problem_id: major.get(f"/api/v2/major-problems/{problem_id}/context")
    )
    integrated = IntegratedP04Provider(LiveQualityScenarioProvider(_rows()[:1]), client)
    snapshot = integrated.snapshot()
    assert snapshot.state == P04State.ERROR
    assert "CONTRACT_PAYLOAD_INVALID" in snapshot.warnings
