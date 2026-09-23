from __future__ import annotations

from pathlib import Path

from presentation.construction.report_builder import ReportBuilder
from presentation.renderer.markdown_renderer import MarkdownRenderer


def _analysis() -> dict:
    return {
        "metadata": {"query_id": "Q1"},
        "final_decision": "LIKELY_REPEAT",
        "overall_confidence": 0.88,
        "best_case": {"case_id": "C1", "final_rank": 1},
        "candidates": [{
            "case_id": "C1",
            "retrieval_rank": 1,
            "final_rank": 1,
            "final_score": 91.5,
            "decision": "LIKELY_REPEAT",
            "confidence": 0.88,
            "decision_reason": "关键机制一致",
            "evidence_chain": ["CAN接收队列发生拥堵"],
            "key_differences": ["触发负载不同"],
            "validation_required": ["确认总线负载"],
            "risks": ["高负载下可能重启"],
            "recommended_actions": ["复用接收限流方案"],
        }],
        "analysis_status": "SUCCESS",
        "warnings": [{"code": "SAMPLE_WARNING", "message": "仅用于测试"}],
    }


def test_render_markdown_contains_report_sections() -> None:
    report = ReportBuilder().build(_analysis())
    content = MarkdownRenderer().render(report)

    assert content.startswith("# 重复问题辅助分析报告（Q1）")
    assert "## 1. AI初步判断" in content
    assert "## 1. AI初步判断" in content
    assert "疑似重复" in content
    assert "## 2. 推荐案例摘要" in content
    assert "复用接收限流方案" in content
    assert "CAN接收队列发生拥堵" in content
    assert "## 9. 风险提示" in content
    assert content.endswith("\n")


def test_render_to_file_writes_utf8(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "report.md"
    report = ReportBuilder().build(_analysis())

    result = MarkdownRenderer().render_to_file(report, target)

    assert result == target
    assert target.exists()
    assert "重复问题辅助分析报告" in target.read_text(encoding="utf-8")


def test_render_accepts_mapping_and_handles_empty_sections() -> None:
    content = MarkdownRenderer().render({
        "summary": {"query_id": "Q2", "analysis_status": "SKIPPED"},
        "repeat_decision": {"decision": "INSUFFICIENT_EVIDENCE", "confidence": 0},
    })

    assert "证据不足" in content
    assert "当前没有可推荐的历史案例" in content
    assert "当前证据不足，未形成明确推荐原因" in content
    assert "暂无可对比信息" in content
