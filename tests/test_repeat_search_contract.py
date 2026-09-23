from __future__ import annotations

from copy import deepcopy

import pytest

from quality_knowledge.repeat_risk import (
    SEARCH_INCOMPLETE,
    SEARCH_NO_CANDIDATES,
    SEARCH_SUCCESS,
    SEARCH_UNAVAILABLE,
    RepeatHistoricalCaseSearchService,
    RepeatQueryTraceRepository,
    RepeatSearchContractError,
)
from services.historical_case_contract import HistoricalCaseContractError


class FakeHistoricalCaseClient:
    def __init__(self):
        self.search_result = {
            "contract_version": "historical-case/v1",
            "candidates": [],
        }
        self.details = {}
        self.search_error = None
        self.detail_errors = {}
        self.last_query = None
        self.last_top_k = None

    def search_repeat_cases(self, query, *, top_k=None):
        self.last_query = query
        self.last_top_k = top_k
        if self.search_error:
            raise HistoricalCaseContractError(self.search_error)
        return deepcopy(self.search_result)

    def get_case(self, case_id):
        error = self.detail_errors.get(case_id)
        if error:
            raise HistoricalCaseContractError(error)
        return deepcopy(self.details[case_id])


def _repository(tmp_path):
    return RepeatQueryTraceRepository(tmp_path / "repeat-risk.sqlite3")


def _save_trace(
    repository,
    *,
    query_id="RQ-1",
    include_missed_test=False,
    optional_context=None,
):
    return repository.save(
        {
            "query_id": query_id,
            "subject_ref": "ITR-1001",
            "itr_version": "ITR-V7",
            "itr_snapshot": {
                "itr_id": "ITR-1001",
                "problem_description": "控制器运行中周期性重启",
                "product": "PLC",
                "version": "V2.3",
                "scene": "长稳运行",
                "existing_context": {
                    "root_cause": "CAN 接收队列拥堵",
                    "action": "临时限制报文速率",
                    "domain": "SOFTWARE",
                },
                "itr_version": "ITR-V7",
                "source": "ITR_RESOLUTION_WORKBENCH",
            },
            "include_missed_test": include_missed_test,
            "missed_test_ref": "MISS-9" if include_missed_test else None,
            "optional_context": optional_context,
            "query_time": "2026-09-23T12:00:00+00:00",
            "algorithm_version": "repeat-risk/test-v1",
            "correlation_id": "CORR-1",
        }
    )


def _candidate():
    return {
        "case_id": "CASE-H-1",
        "title": "历史控制器重启",
        "summary": "控制器重启案例",
        "score": 0.91,
        "rank": 1,
        "retrieval_reason": ["问题现象高度相似", "原因描述相似"],
        "matched_fields": ["problem", "cause"],
    }


def _detail(evidence=None):
    return {
        "contract_version": "historical-case/v1",
        "case_id": "CASE-H-1",
        "title": "历史控制器重启",
        "problem_description": "历史控制器因报文拥堵发生重启",
        "product": "PLC",
        "device_type": None,
        "device_model": None,
        "symptom": "运行中周期性重启",
        "root_cause": "接收队列缺少流控",
        "solution": "增加队列水位保护",
        "verification_result": "长稳回归通过",
        "status": "ACTIVE",
        "evidence": evidence
        if evidence is not None
        else [
            {
                "source_type": "REPORT",
                "source_id": "ITR-H-1",
                "file_name": "history.pdf",
                "page": 3,
                "section": "root_cause",
                "raw_text": "接收队列缺少流控。",
                "url": None,
            }
        ],
    }


def test_search_uses_itr_snapshot_and_historical_case_contract(tmp_path):
    repository = _repository(tmp_path)
    _save_trace(
        repository,
        include_missed_test=True,
        optional_context={
            "missed_test_ref": "MISS-9",
            "snapshot": {
                "description": "异常场景漏测",
                "test_gap": "缺少高报文负载场景",
            },
        },
    )
    client = FakeHistoricalCaseClient()
    client.search_result["candidates"] = [_candidate()]
    client.details["CASE-H-1"] = _detail()

    result = RepeatHistoricalCaseSearchService(repository, client).search("RQ-1", top_k=3)

    assert result["status"] == SEARCH_SUCCESS
    assert result["historical_case_contract"] == "historical-case/v1"
    assert client.last_top_k == 3
    assert client.last_query.text == "控制器运行中周期性重启\n长稳运行"
    assert client.last_query.product == "PLC"
    assert "CAN 接收队列拥堵" in client.last_query.cause_description
    assert "异常场景漏测" in client.last_query.cause_description
    assert "高报文负载场景" in client.last_query.cause_description
    assert client.last_query.solution == "临时限制报文速率"

    candidate = result["candidates"][0]
    assert candidate["case_id"] == "CASE-H-1"
    assert candidate["retrieval_score"] == 0.91
    assert candidate["historical_phenomenon"] == "运行中周期性重启"
    assert candidate["root_causes"] == ["接收队列缺少流控"]
    assert candidate["measures"] == ["增加队列水位保护"]
    assert candidate["verification"] == "长稳回归通过"
    assert candidate["source_ref"] == "ITR-H-1"
    assert candidate["evidence_refs"][0]["page"] == 3
    assert candidate["evidence"][0]["raw_text"] == "接收队列缺少流控。"


def test_unselected_missed_test_never_enters_search_query(tmp_path):
    repository = _repository(tmp_path)
    _save_trace(
        repository,
        include_missed_test=False,
        optional_context={
            "missed_test_ref": "MISS-9",
            "snapshot": {"description": "不应进入检索的漏测内容"},
        },
    )
    client = FakeHistoricalCaseClient()

    result = RepeatHistoricalCaseSearchService(repository, client).search("RQ-1")

    assert result["status"] == SEARCH_NO_CANDIDATES
    assert "不应进入检索的漏测内容" not in client.last_query.cause_description
    assert client.last_query.text.startswith("控制器运行中周期性重启")


def test_successful_search_with_no_candidates_is_not_not_repeat(tmp_path):
    repository = _repository(tmp_path)
    _save_trace(repository)
    client = FakeHistoricalCaseClient()

    result = RepeatHistoricalCaseSearchService(repository, client).search("RQ-1")

    assert result["status"] == SEARCH_NO_CANDIDATES
    assert result["candidates"] == []
    assert "NOT_REPEAT" not in str(result)


def test_search_service_error_is_search_unavailable_not_not_repeat(tmp_path):
    repository = _repository(tmp_path)
    _save_trace(repository)
    client = FakeHistoricalCaseClient()
    client.search_error = "CASE_SERVICE_UNAVAILABLE"

    result = RepeatHistoricalCaseSearchService(repository, client).search("RQ-1")

    assert result["status"] == SEARCH_UNAVAILABLE
    assert result["error_code"] == "CASE_SERVICE_UNAVAILABLE"
    assert result["candidates"] == []
    assert "NOT_REPEAT" not in str(result)


def test_invalid_search_contract_is_incomplete(tmp_path):
    repository = _repository(tmp_path)
    _save_trace(repository)
    client = FakeHistoricalCaseClient()
    client.search_result = {
        "contract_version": "historical-case/v0",
        "candidates": [_candidate()],
    }

    result = RepeatHistoricalCaseSearchService(repository, client).search("RQ-1")

    assert result["status"] == SEARCH_INCOMPLETE
    assert result["error_code"] == "CASE_CONTRACT_INVALID"


def test_candidate_detail_failure_keeps_candidate_and_marks_incomplete(tmp_path):
    repository = _repository(tmp_path)
    _save_trace(repository)
    client = FakeHistoricalCaseClient()
    client.search_result["candidates"] = [_candidate()]
    client.detail_errors["CASE-H-1"] = "CASE_NOT_FOUND"

    result = RepeatHistoricalCaseSearchService(repository, client).search("RQ-1")

    assert result["status"] == SEARCH_INCOMPLETE
    assert result["error_code"] == "CANDIDATE_DETAIL_INCOMPLETE"
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["case_id"] == "CASE-H-1"
    assert result["candidates"][0]["detail_status"] == SEARCH_INCOMPLETE
    assert result["candidates"][0]["detail_error"] == "CASE_NOT_FOUND"


def test_missing_evidence_never_guesses_source_ref(tmp_path):
    repository = _repository(tmp_path)
    _save_trace(repository)
    client = FakeHistoricalCaseClient()
    client.search_result["candidates"] = [_candidate()]
    client.details["CASE-H-1"] = _detail(evidence=[])

    result = RepeatHistoricalCaseSearchService(repository, client).search("RQ-1")

    candidate = result["candidates"][0]
    assert result["status"] == SEARCH_SUCCESS
    assert candidate["evidence_refs"] == []
    assert candidate["source_ref"] is None
    assert candidate["source_refs"] == []


def test_unknown_query_id_fails_closed(tmp_path):
    repository = _repository(tmp_path)
    client = FakeHistoricalCaseClient()

    with pytest.raises(RepeatSearchContractError) as error:
        RepeatHistoricalCaseSearchService(repository, client).search("RQ-NOT-FOUND")

    assert error.value.code == "REPEAT_QUERY_NOT_FOUND"
