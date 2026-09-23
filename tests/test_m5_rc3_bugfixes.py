from presentation.construction.report_builder import ReportBuilder
from presentation.renderer.markdown_renderer import MarkdownRenderer


def _analysis():
    return {
        "query_id": "Q-RC3",
        "analysis_status": "COMPLETED",
        "final_decision": "LIKELY_REPEAT",
        "overall_confidence": 0.75,
        "best_case": {"case_id": "CASE-1"},
        "candidates": [{
            "case_id": "CASE-1",
            "decision": "LIKELY_REPEAT",
            "confidence": 0.75,
            "comparison_context": {
                "query": {"standard_query": {
                    "一级原因分类": {"value": "软件", "source_type": "AI", "confidence": 0.8},
                    "二级原因分类": "设计",
                    "TRC": {"original": "接收拥堵", "normalized": "接收处理拥堵", "confidence": 0.7},
                }},
                "case": {"enriched_case": {
                    "原因一级分类": "软件",
                    "原因二级分类": "设计",
                    "TRC": "控制位判断异常导致拥堵",
                }},
            },
            "similarity": {"analysis": {}},
            "solution": {"analysis": {}},
            "evidence_chain": [{"value": "历史问题描述一致", "source_type": "AI", "confidence": 0.75}],
        }],
        "warnings": [],
    }


def test_reason_labels_are_canonical_and_old_input_is_compatible():
    report = ReportBuilder().build(_analysis())
    rows = report.comparison["reason_compare"]
    dimensions = [row["dimension"] for row in rows]
    assert dimensions[:4] == ["原因一级分类", "原因二级分类", "原因三级分类", "原因四级分类"]
    assert rows[0]["current"] == "软件"
    assert rows[0]["historical"] == "软件"


def test_markdown_does_not_leak_internal_dto_fields_and_uses_unprovided():
    content = MarkdownRenderer().render(ReportBuilder().build(_analysis()))
    assert "原因一级分类" in content
    assert "一级原因分类" not in content.replace("原因一级分类", "")
    assert "历史问题描述一致" in content
    for forbidden in ("source_type", "confidence：", "original：", "normalized：", "{'value'", "\"value\""):
        assert forbidden not in content
    assert "未提供" in content


def test_reason_classification_is_read_from_real_nested_query_and_case_models():
    analysis = _analysis()
    candidate = analysis["candidates"][0]
    candidate["comparison_context"] = {
        "query": {
            "standard_query": {
                "classification": {
                    "cause_level1": {"effective": "软件设计", "original": "软件设计"},
                    "cause_level2": {"effective": "资源管理", "original": "资源管理"},
                    "cause_level3": {"effective": "消息队列", "original": "消息队列"},
                    "cause_level4": {"effective": "CAN接收缓存", "original": "CAN接收缓存"},
                }
            }
        },
        "case": {
            "enriched_case": {
                "classification": {
                    "original": {
                        "cause_level1": "研发产品设计问题",
                        "cause_level2": "软件子系统",
                        "cause_level3": "软件设计问题",
                        "cause_level4": "设计方案不合理/错误（功能）",
                    }
                }
            }
        },
    }

    rows = ReportBuilder().build(analysis).comparison["reason_compare"]
    assert [row["current"] for row in rows[:4]] == [
        "软件设计", "资源管理", "消息队列", "CAN接收缓存"
    ]
    assert [row["historical"] for row in rows[:4]] == [
        "研发产品设计问题", "软件子系统", "软件设计问题", "设计方案不合理/错误（功能）"
    ]


def test_root_cause_and_solution_comparison_are_business_readable():
    analysis = _analysis()
    candidate = analysis["candidates"][0]
    candidate["solution"] = {
        "analysis": {
            "historical_solution_summary": "升级软件并修正抱闸控制逻辑",
            "corrective_actions": [
                {"corrective_action": "修正控制逻辑", "solution_object": "抱闸控制"}
            ],
            "preventive_actions": [
                {"preventive_action": "补充异常场景测试", "expected_effect": "防止同类问题复发"}
            ],
            "reusable_actions": ["复用异常场景测试用例"],
            "adaptation_required": ["结合当前功能对象调整触发条件"],
            "effectiveness": "有效",
            "applicability": "需适配",
        }
    }
    candidate["recommended_actions"] = ["补充自动化回归测试"]

    content = MarkdownRenderer().render(ReportBuilder().build(analysis))

    for expected in (
        "当前问题根因", "历史案例根因", "根因分析结论",
        "当前整改措施", "历史整改措施", "历史纠正措施", "历史预防措施",
        "建议补充措施", "措施对比结论", "修正控制逻辑", "补充异常场景测试",
    ):
        assert expected in content

    for forbidden in (
        "corrective_action", "preventive_action", "solution_object",
        "solution_mechanism", "effective_source", "expected_effect",
    ):
        assert forbidden not in content
