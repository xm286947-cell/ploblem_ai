from __future__ import annotations

import json
from pathlib import Path

import pytest

from repositories import JsonArtifactRepository
from retriever.case_retriever import QueryInput
from services import (
    CONTRACT_VERSION,
    HistoricalCaseConsumerService,
    HistoricalCaseContractError,
)


def _save_case(
    root: Path,
    case_id: str = "CASE-H-1",
    *,
    root_cause: bool = True,
    solution: bool = True,
    page: int | None = 3,
    extra: dict | None = None,
) -> HistoricalCaseConsumerService:
    repository = JsonArtifactRepository(root)
    repository.save("knowledge/retrieval_docs/CASE-H-1.json", {
        "case_id": case_id,
        "title": "历史控制器重启案例",
        "text": "控制器因报文拥堵重启",
        "source_case_path": "knowledge/enriched_case/CASE-H-1.json",
    })
    analysis = {"root_cause": [{"value": "CAN 接收队列没有流控"}]} if root_cause else {"root_cause": []}
    actions = [{"value": "增加队列水位保护"}] if solution else []
    case = {
        "metadata": {"case_id": case_id, "itr_id": "ITR-H-1", "parse_status": "SUCCESS", "report_filename": "history.pdf"},
        "business_context": {"product": "控制器", "device_type": "PLC", "device_model": "X1"},
        "problem": {
            "standard_description": "控制器因报文拥堵重启",
            "phenomenon": [{"value": "周期性重启"}],
        },
        "analysis": analysis,
        "solution": {"corrective_actions": actions, "preventive_actions": [], "reusable_actions": []},
        "unknown_future_field": extra or {"ignored": True},
    }
    repository.save("knowledge/enriched_case/CASE-H-1.json", case)
    section = {
        "section_type": "root_cause",
        "content": "报告确认 CAN 接收队列没有流控。",
        "page_numbers": [] if page is None else [page],
        "unknown_future_field": "ignored",
    }
    repository.save("knowledge/raw_evidence/CASE-H-1.json", {
        "case_id": case_id,
        "report_filename": "history.pdf",
        "sections": [section],
        "unknown_future_field": "ignored",
    })

    def search(_query: QueryInput, _top_k: int | None) -> dict:
        return {"results": [{
            "case_id": case_id,
            "title": "历史控制器重启案例",
            "score": 0.91,
            "rank": 1,
            "reasons": ["问题现象高度相似"],
            "retrieval_doc_path": "knowledge/retrieval_docs/CASE-H-1.json",
            "internal_path": "/not-for-consumers",
        }]}

    return HistoricalCaseConsumerService(repository, repeat_search=search)


def test_case_01_search_returns_stable_case_id(tmp_path: Path) -> None:
    service = _save_case(tmp_path)
    first = service.search_repeat_cases(QueryInput(text="控制器重启"))
    second = service.search_repeat_cases(QueryInput(text="控制器重启"))
    assert first["contract_version"] == CONTRACT_VERSION
    assert first["candidates"][0]["case_id"] == second["candidates"][0]["case_id"] == "CASE-H-1"


def test_case_02_get_existing_case(tmp_path: Path) -> None:
    detail = _save_case(tmp_path).get_case("CASE-H-1")
    assert detail["case_id"] == "CASE-H-1"
    assert detail["problem_description"] == "控制器因报文拥堵重启"
    assert detail["root_cause"] == "CAN 接收队列没有流控"
    assert detail["solution"] == "增加队列水位保护"


def test_case_03_case_not_found(tmp_path: Path) -> None:
    service = _save_case(tmp_path)
    with pytest.raises(HistoricalCaseContractError, match="CASE_NOT_FOUND") as error:
        service.get_case("CASE-MISSING")
    assert error.value.code == "CASE_NOT_FOUND"


def test_case_04_root_cause_missing_is_not_case_failure(tmp_path: Path) -> None:
    detail = _save_case(tmp_path, root_cause=False).get_case("CASE-H-1")
    assert detail["root_cause"] is None


def test_case_05_solution_missing_is_not_case_failure(tmp_path: Path) -> None:
    detail = _save_case(tmp_path, solution=False).get_case("CASE-H-1")
    assert detail["solution"] is None


def test_case_06_evidence_is_complete_and_traceable(tmp_path: Path) -> None:
    evidence = _save_case(tmp_path).get_case("CASE-H-1")["evidence"]
    assert evidence == [{
        "source_type": "REPORT",
        "source_id": "ITR-H-1",
        "file_name": "history.pdf",
        "page": 3,
        "section": "root_cause",
        "raw_text": "报告确认 CAN 接收队列没有流控。",
        "url": None,
    }]


def test_case_07_page_unavailable_is_null(tmp_path: Path) -> None:
    evidence = _save_case(tmp_path, page=None).get_case("CASE-H-1")["evidence"]
    assert evidence[0]["page"] is None


def test_case_08_unknown_fields_are_backward_compatible(tmp_path: Path) -> None:
    detail = _save_case(tmp_path, extra={"new_field": "future"}).get_case("CASE-H-1")
    assert "unknown_future_field" not in detail
    assert detail["case_id"] == "CASE-H-1"


def test_case_09_search_candidate_to_detail_round_trip(tmp_path: Path) -> None:
    service = _save_case(tmp_path)
    candidate = service.search_repeat_cases(QueryInput(text="控制器重启"))["candidates"][0]
    detail = service.get_case(candidate["case_id"])
    assert detail["case_id"] == candidate["case_id"]
    assert detail["evidence"]


def test_case_10_consumer_never_needs_internal_path(tmp_path: Path) -> None:
    service = _save_case(tmp_path)
    search = service.search_repeat_cases(QueryInput(text="控制器重启"))
    detail = service.get_case(search["candidates"][0]["case_id"])
    public_payload = json.dumps({"search": search, "detail": detail}, ensure_ascii=False)
    assert "internal_path" not in public_payload
    assert "retrieval_doc_path" not in public_payload
    assert "source_case_path" not in public_payload
