from __future__ import annotations

import pytest

from quality_knowledge.repeat_risk import (
    RESULT_INCOMPLETE,
    RESULT_NO_CANDIDATES,
    RESULT_READY_FOR_REVIEW,
    RESULT_SEARCH_UNAVAILABLE,
    SEARCH_INCOMPLETE,
    SEARCH_NO_CANDIDATES,
    SEARCH_SUCCESS,
    SEARCH_UNAVAILABLE,
    RepeatQueryTraceRepository,
    RepeatResultContractError,
    RepeatResultService,
)


def _repository(tmp_path):
    return RepeatQueryTraceRepository(tmp_path / "repeat-risk.sqlite3")


def _save_trace(repository, query_id="RQ-1", include_missed_test=True):
    repository.save(
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
                "existing_context": {},
                "itr_version": "ITR-V7",
                "source": "ITR_RESOLUTION_WORKBENCH",
            },
            "include_missed_test": include_missed_test,
            "missed_test_ref": "MISS-9" if include_missed_test else None,
            "optional_context": (
                {
                    "missed_test_ref": "MISS-9",
                    "snapshot": {"description": "高负载场景漏测"},
                }
                if include_missed_test
                else None
            ),
            "query_time": "2026-09-23T13:00:00+00:00",
            "algorithm_version": "repeat-risk/v1",
            "correlation_id": "CORR-1001",
        }
    )


def _search_candidate(*, reasons=None, matched=None, detail_status=SEARCH_SUCCESS):
    return {
        "case_id": "CASE-H-1",
        "title": "历史控制器重启",
        "summary": "历史控制器重启案例",
        "retrieval_score": 0.91,
        "rank": 1,
        "retrieval_reason": (
            ["问题现象高度相似", "原因描述相似"] if reasons is None else reasons
        ),
        "matched_fields": ["problem", "cause"] if matched is None else matched,
        "historical_phenomenon": "运行中周期性重启",
        "problem_description": "历史控制器因报文拥堵发生重启",
        "product": "PLC",
        "root_causes": ["接收队列缺少流控"],
        "measures": ["增加队列水位保护"],
        "verification": "长稳回归通过",
        "evidence_refs": [
            {
                "source_type": "REPORT",
                "source_id": "ITR-H-1",
                "file_name": "history.pdf",
                "page": 3,
                "section": "root_cause",
                "url": None,
            }
        ],
        "evidence": [
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
        "source_ref": "ITR-H-1",
        "source_refs": ["ITR-H-1"],
        "detail_status": detail_status,
        "detail_error": None if detail_status == SEARCH_SUCCESS else "CASE_NOT_FOUND",
        "case_status": "ACTIVE",
    }


def _search_result(status=SEARCH_SUCCESS, candidates=None, error_code=None):
    return {
        "query_id": "RQ-1",
        "subject_ref": "ITR-1001",
        "query_input": {"text": "控制器重启"},
        "historical_case_contract": "historical-case/v1",
        "status": status,
        "candidates": [] if candidates is None else candidates,
        "error_code": error_code,
    }


def test_result_is_reviewable_and_why_relevant_is_independent_from_score(tmp_path):
    repository = _repository(tmp_path)
    _save_trace(repository)
    service = RepeatResultService(repository)

    result = service.build(
        _search_result(candidates=[_search_candidate()])
    )

    assert result["contract_version"] == "repeat-result/v1"
    assert result["result_status"] == RESULT_READY_FOR_REVIEW
    assert result["candidate_count"] == 1
    assert result["human_decision"]["decision"] == "PENDING"
    candidate = result["candidates"][0]
    assert candidate["retrieval_score"] == 0.91
    assert candidate["explanation_status"] == "EXPLAINED"
    assert {item["type"] for item in candidate["why_relevant"]} == {
        "RETRIEVAL_REASON",
        "MATCHED_FIELD",
    }
    assert any(item["text"] == "问题现象高度相似" for item in candidate["why_relevant"])
    assert any(item["text"] == "匹配维度：问题现象" for item in candidate["why_relevant"])
    assert candidate["root_causes"] == ["接收队列缺少流控"]
    assert candidate["measures"] == ["增加队列水位保护"]
    assert candidate["verification"] == "长稳回归通过"
    assert candidate["evidence"][0]["raw_text"] == "接收队列缺少流控。"
    assert candidate["source_ref"] == "ITR-H-1"


def test_score_only_candidate_is_marked_explanation_insufficient(tmp_path):
    repository = _repository(tmp_path)
    _save_trace(repository)
    service = RepeatResultService(repository)

    result = service.build(
        _search_result(
            candidates=[_search_candidate(reasons=[], matched=[])]
        )
    )

    candidate = result["candidates"][0]
    assert candidate["retrieval_score"] == 0.91
    assert candidate["why_relevant"] == []
    assert candidate["explanation_status"] == "INSUFFICIENT"
    assert "不能仅依据相似度分数判断 Repeat" in candidate["explanation_message"]
    assert any(
        item["code"] == "RELEVANCE_EXPLANATION_INSUFFICIENT"
        for item in result["warnings"]
    )


def test_no_candidates_never_becomes_not_repeat(tmp_path):
    repository = _repository(tmp_path)
    _save_trace(repository)
    service = RepeatResultService(repository)

    result = service.build(_search_result(status=SEARCH_NO_CANDIDATES))

    assert result["result_status"] == RESULT_NO_CANDIDATES
    assert result["candidate_count"] == 0
    assert result["human_decision"]["decision"] == "PENDING"
    assert any(item["code"] == "NO_CANDIDATES" for item in result["warnings"])
    assert "NOT_REPEAT" not in str(result["result_status"])


def test_search_unavailable_never_becomes_not_repeat(tmp_path):
    repository = _repository(tmp_path)
    _save_trace(repository)
    service = RepeatResultService(repository)

    result = service.build(
        _search_result(
            status=SEARCH_UNAVAILABLE,
            error_code="CASE_SERVICE_UNAVAILABLE",
        )
    )

    assert result["result_status"] == RESULT_SEARCH_UNAVAILABLE
    assert result["search_error"] == "CASE_SERVICE_UNAVAILABLE"
    assert result["human_decision"]["decision"] == "PENDING"
    assert any(item["code"] == "SEARCH_UNAVAILABLE" for item in result["warnings"])


def test_incomplete_candidate_stays_visible_with_warning(tmp_path):
    repository = _repository(tmp_path)
    _save_trace(repository)
    service = RepeatResultService(repository)

    result = service.build(
        _search_result(
            status=SEARCH_INCOMPLETE,
            candidates=[_search_candidate(detail_status=SEARCH_INCOMPLETE)],
            error_code="CANDIDATE_DETAIL_INCOMPLETE",
        )
    )

    assert result["result_status"] == RESULT_INCOMPLETE
    assert result["candidate_count"] == 1
    assert result["candidates"][0]["case_id"] == "CASE-H-1"
    assert result["candidates"][0]["detail_status"] == SEARCH_INCOMPLETE
    assert any(item["code"] == "CANDIDATE_DETAIL_INCOMPLETE" for item in result["warnings"])


def test_result_snapshot_restores_without_rebuilding_search(tmp_path):
    repository = _repository(tmp_path)
    _save_trace(repository)
    service = RepeatResultService(repository)

    first = service.build(_search_result(candidates=[_search_candidate()]))
    restored = service.get("RQ-1")
    second = service.build(
        _search_result(
            status=SEARCH_NO_CANDIDATES,
            candidates=[],
        )
    )

    assert restored["generated_at"] == first["generated_at"]
    assert second["result_status"] == RESULT_READY_FOR_REVIEW
    assert second["candidate_count"] == 1
    assert second["generated_at"] == first["generated_at"]


@pytest.mark.parametrize(
    "decision",
    ["REPEAT", "SIMILAR", "NOT_REPEAT", "INSUFFICIENT_EVIDENCE"],
)
def test_human_decision_is_explicit_and_persisted(tmp_path, decision):
    repository = _repository(tmp_path)
    _save_trace(repository)
    service = RepeatResultService(repository)
    service.build(_search_result(candidates=[_search_candidate()]))

    decided = service.decide(
        "RQ-1",
        decision,
        decided_by="quality-owner",
        reason="人工核对历史 Evidence 后判断",
    )
    restored = service.get("RQ-1")

    assert decided["human_decision"]["decision"] == decision
    assert decided["human_decision"]["decided_by"] == "quality-owner"
    assert decided["human_decision"]["reason"] == "人工核对历史 Evidence 后判断"
    assert decided["human_decision"]["decided_at"]
    assert restored["human_decision"] == decided["human_decision"]


def test_invalid_human_decision_fails_closed(tmp_path):
    repository = _repository(tmp_path)
    _save_trace(repository)
    service = RepeatResultService(repository)
    service.build(_search_result(candidates=[_search_candidate()]))

    with pytest.raises(RepeatResultContractError) as error:
        service.decide("RQ-1", "AI_AUTO_REPEAT", decided_by="system")

    assert error.value.code == "INVALID_REPEAT_DECISION"


def test_result_preserves_query_trace_for_optional_context_audit(tmp_path):
    repository = _repository(tmp_path)
    _save_trace(repository, include_missed_test=True)
    service = RepeatResultService(repository)

    result = service.build(_search_result(candidates=[_search_candidate()]))

    assert result["subject_ref"] == "ITR-1001"
    assert result["correlation_id"] == "CORR-1001"
    assert result["query_snapshot"] == {
        "itr_version": "ITR-V7",
        "include_missed_test": True,
        "missed_test_ref": "MISS-9",
        "algorithm_version": "repeat-risk/v1",
    }
