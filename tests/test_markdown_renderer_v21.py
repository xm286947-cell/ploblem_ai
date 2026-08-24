from presentation.construction.report_builder import ReportBuilder
from presentation.renderer.markdown_renderer import MarkdownRenderer
from tests.test_report_builder_v21 import _analysis


def test_markdown_v21_is_detailed_and_hides_internal_rank() -> None:
    content = MarkdownRenderer().render(ReportBuilder().build(_analysis()))
    assert content.startswith("# 重复问题辅助分析报告（Q1）")
    assert "## 4. 原因分类与问题对象对比" in content
    assert "原因一级分类" in content
    assert "## 5. 问题根因对比" in content
    assert "## 6. 改进措施对比" in content
    assert "## 7. 建议人工确认" in content
    assert "复核控制位状态" in content
    assert "检索排名" not in content
    assert "最终得分" not in content
    assert "所属部门" not in content
