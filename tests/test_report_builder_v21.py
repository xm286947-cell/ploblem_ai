from presentation.construction.report_builder import ReportBuilder


def _analysis() -> dict:
    return {
        "metadata": {"query_id": "Q1"},
        "final_decision": "LIKELY_REPEAT",
        "overall_confidence": 0.88,
        "best_case": {"case_id": "C1", "final_rank": 1},
        "candidates": [{
            "case_id": "C1", "retrieval_rank": 1, "final_rank": 1, "final_score": 90,
            "decision": "LIKELY_REPEAT", "confidence": 0.88,
            "decision_reason": "根因方向一致", "evidence_chain": [],
            "key_differences": ["四级分类待确认"],
            "validation_required": ["复核控制位状态"], "risks": ["禁止直接照搬措施"],
            "recommended_actions": ["参考历史压力测试"],
            "comparison_context": {
                "query": {"standard_query": {"一级原因分类": "软件", "二级原因分类": "设计", "三级原因分类": "通信", "四级原因分类": "控制位", "TRC": "接收拥堵"}},
                "case": {"enriched_case": {"一级原因分类": "软件", "二级原因分类": "设计", "三级原因分类": "通信", "四级原因分类": "队列", "TRC": "控制位异常导致拥堵"}},
            },
            "similarity": {"analysis": {"dimensions": {"root_cause": {"assessment": "SIMILAR", "reason": "根因语义相近"}}, "key_similarities": ["触发条件一致"]}},
            "solution": {"analysis": {"historical_solution_summary": "修正控制位并增加保护", "corrective_actions": ["修正控制位"], "reusable_actions": ["复用压力测试"], "reuse_risks": ["根因不同则措施无效"], "applicability": "PARTIAL_REUSE", "effectiveness": "EFFECTIVE"}},
        }],
        "analysis_status": "SUCCESS", "warnings": [],
    }


def test_report_v21_contains_human_review_comparison() -> None:
    report = ReportBuilder().build(_analysis()).to_dict()
    assert report["metadata"]["contract_version"] == "2.1"
    assert report["summary"]["human_confirmation_required"] is True
    assert report["recommended_case"]["case_id"] == "C1"
    rows = report["comparison"]["reason_compare"]
    assert any(row["dimension"] == "原因一级分类" and row["status"] == "一致" for row in rows)
    assert any(row["dimension"] == "原因四级分类" and row["status"] == "不一致" for row in rows)
    assert report["comparison"]["root_cause_compare"]["status"] == "相似"
    assert "复核控制位状态" in report["comparison"]["checklist"]
