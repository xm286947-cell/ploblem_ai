from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from presentation.contract.report import Report


class MarkdownRenderer:
    """Render the repeat-case report for human confirmation."""

    DECISION_LABELS = {
        "REPEAT_CASE": "重复问题",
        "CONFIRMED_REPEAT": "确认重复",
        "LIKELY_REPEAT": "疑似重复",
        "RELATED_CASE": "相关问题",
        "NEW_CASE": "新问题",
        "NOT_REPEAT": "非重复",
        "INSUFFICIENT_EVIDENCE": "证据不足",
    }
    STATUS_LABELS = {"一致": "✅ 一致", "高度相似": "✅ 高度相似", "相似": "⚠ 相似", "不一致": "⚠ 不一致", "缺失": "⚠ 信息缺失", "待确认": "⚠ 待确认"}
    ENUM_LABELS = {
        "PARTIAL": "部分闭环", "COMPLETE": "已闭环", "UNKNOWN": "未知",
        "NOT_APPLICABLE": "不适用", "DIRECT_REUSE": "可直接复用",
        "PARTIAL_REUSE": "部分复用", "REFERENCE_ONLY": "仅供参考",
    }
    DIMENSION_LABELS = {
        "problem_object": "问题对象", "phenomenon": "问题现象", "trigger_condition": "触发条件",
        "impact": "问题影响", "failure_mechanism": "失效机理", "trc": "技术根因（TRC）",
        "mrc": "管理根因（MRC）", "root_cause": "问题根因", "classification": "原因分类",
        "solution": "整改措施", "measure": "整改措施",
    }

    def render(self, report: Report | Mapping[str, Any]) -> str:
        value = report if isinstance(report, Report) else self._report_from_mapping(report)
        qid = self._text(value.summary.get("query_id")) or self._text(value.traceability.get("query_id"))
        title = "重复问题辅助分析报告" + (f"（{qid}）" if qid else "")
        lines = [f"# {title}", "", "> 本报告由智能体生成，用于辅助人工判断是否属于重复问题；最终结论需由分析人员确认。", ""]
        self._append_initial_decision(lines, value)
        self._append_recommended_case(lines, value)
        self._append_recommendation_reasons(lines, value)
        self._append_evidence_chain(lines, value)
        self._append_reason_comparison(lines, value)
        self._append_root_cause_comparison(lines, value)
        self._append_solution_comparison(lines, value)
        self._append_checklist(lines, value)
        self._append_other_cases(lines, value)
        self._append_risks(lines, value)
        self._append_traceability(lines, value)
        return "\n".join(lines).rstrip() + "\n"

    def render_to_file(self, report: Report | Mapping[str, Any], output_path: str | Path) -> Path:
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.render(report), encoding="utf-8")
        return target

    def _append_initial_decision(self, lines: list[str], report: Report) -> None:
        decision = report.repeat_decision
        code = self._text(decision.get("decision")) or "INSUFFICIENT_EVIDENCE"
        best = decision.get("best_case") if isinstance(decision.get("best_case"), Mapping) else {}
        lines.extend([
            "## 1. AI初步判断",
            "",
            "| 项目 | 内容 |",
            "|---|---|",
            f"| 初步判断 | {self._cell(self.DECISION_LABELS.get(code, code))}（待人工确认） |",
            f"| 综合置信度 | {self._cell(self._percent(decision.get('confidence')))} |",
            f"| 推荐优先核查案例 | {self._cell(best.get('case_id') or report.recommended_case.get('case_id') or '无')} |",
            f"| 说明 | {self._cell(decision.get('notice') or 'AI结论仅供辅助判断')} |",
            "",
        ])

    def _append_recommended_case(self, lines: list[str], report: Report) -> None:
        case = report.recommended_case
        lines.extend(["## 2. 推荐案例摘要", ""])
        if not case:
            lines.extend(["当前没有可推荐的历史案例。", ""])
            return
        lines.extend([
            "| 项目 | 内容 |",
            "|---|---|",
            f"| 案例编号 | {self._cell(case.get('case_id'))} |",
            f"| 候选判断 | {self._cell(self.DECISION_LABELS.get(self._text(case.get('decision')), case.get('decision')))} |",
            f"| 综合相似度 | {self._score(case.get('similarity_score'))} |",
            f"| 判断置信度 | {self._cell(self._percent(case.get('confidence')))} |",
            f"| 推荐等级 | {self._cell(case.get('recommendation_level') or '未评级')} |",
            f"| 判断说明 | {self._cell(case.get('decision_reason'))} |",
            f"| 历史闭环状态 | {self._cell(self._enum(case.get('closure_status')))} |",
            f"| 历史措施有效性 | {self._cell(self._enum(case.get('effectiveness')))} |",
            f"| 措施适用性 | {self._cell(self._enum(case.get('applicability')))} |",
            "",
        ])
        self._append_score_table(lines, case.get("dimension_scores"))
        self._append_confidence_details(lines, case.get("confidence_details"))
        self._append_context_applicability(lines, case.get("context_applicability"))
        self._append_list(lines, "推荐依据", case.get("recommendation_reasons"))
        summary = self._text(case.get("historical_solution_summary"))
        if summary:
            lines.extend(["**历史处理摘要**", "", summary, ""])

    def _append_recommendation_reasons(self, lines: list[str], report: Report) -> None:
        reasons = self._items((report.comparison or {}).get("recommendation_reasons"))
        lines.extend(["## 3. 推荐原因", ""])
        if not reasons:
            lines.extend(["当前证据不足，未形成明确推荐原因。", ""])
            return
        lines.extend(f"- {self._display(item)}" for item in reasons)
        lines.append("")


    def _append_evidence_chain(self, lines: list[str], report: Report) -> None:
        items = report.evidence
        if not items:
            return
        lines.extend(["### 证据链", ""])
        for item in items:
            case_id = self._text(item.get("case_id")) if isinstance(item, Mapping) else ""
            evidence = item.get("evidence") if isinstance(item, Mapping) else item
            prefix = f"**{case_id}**：" if case_id else ""
            lines.append(f"- {prefix}{self._display(evidence)}")
        lines.append("")

    def _append_reason_comparison(self, lines: list[str], report: Report) -> None:
        rows = self._items((report.comparison or {}).get("reason_compare"))
        lines.extend(["## 4. 原因分类与问题对象对比", "", "| 判断维度 | 当前问题 | 历史案例 | 对比结果 |", "|---|---|---|---|"])
        if not rows:
            lines.append("| 未提供 | 未提供 | 未提供 | 暂无可对比信息 |")
        else:
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                status = self._text(row.get("status"))
                lines.append(f"| {self._cell(row.get('dimension'))} | {self._cell(row.get('current'))} | {self._cell(row.get('historical'))} | {self._cell(self.STATUS_LABELS.get(status, status))} |")
        lines.append("")
        lines.extend(["> 原因分类可能存在人工填写偏差。分类一致可作为参考证据，但不能替代问题根因与措施的最终核实。", ""])

    def _append_root_cause_comparison(self, lines: list[str], report: Report) -> None:
        root = (report.comparison or {}).get("root_cause_compare") or {}
        lines.extend([
            "## 5. 问题根因对比",
            "",
            "| 项目 | 内容 |",
            "|---|---|",
            f"| 当前问题根因 | {self._cell(root.get('current'))} |",
            f"| 历史案例根因 | {self._cell(root.get('historical'))} |",
            f"| 对比结果 | {self._cell(root.get('status'))} |",
            f"| 证据状态 | {self._cell(root.get('evidence_status'))} |",
            "",
        ])
        self._append_list(lines, "根因共同点", root.get("common_points"))
        self._append_list(lines, "根因差异点", root.get("difference_points"))
        conclusion = self._text(root.get("analysis_conclusion") or root.get("reason"))
        if conclusion:
            lines.extend(["**根因分析结论**", "", self._display(conclusion), ""])

    def _append_solution_comparison(self, lines: list[str], report: Report) -> None:
        solution = (report.comparison or {}).get("solution_compare") or {}
        lines.extend(["## 6. 改进措施对比", "", "| 项目 | 内容 |", "|---|---|"])
        lines.extend([
            f"| 当前整改措施 | {self._cell(solution.get('current'))} |",
            f"| 历史整改措施 | {self._cell(solution.get('historical'))} |",
            f"| 历史措施有效性 | {self._cell(solution.get('effectiveness'))} |",
            f"| 复用适用性 | {self._cell(solution.get('applicability'))} |",
            "",
        ])
        self._append_list(lines, "历史纠正措施", solution.get("corrective_actions"))
        self._append_list(lines, "历史预防措施", solution.get("preventive_actions"))
        self._append_list(lines, "共同措施", solution.get("common_actions"))
        self._append_list(lines, "差异措施", solution.get("different_actions"))
        self._append_list(lines, "建议补充措施", solution.get("recommended_actions"))
        self._append_list(lines, "可参考复用措施", solution.get("reusable_actions"))
        self._append_list(lines, "复用前需适配", solution.get("adaptation_required"))
        conclusion = self._text(solution.get("analysis_conclusion") or solution.get("analysis_summary"))
        if conclusion:
            lines.extend(["**措施对比结论**", "", self._display(conclusion), ""])

    def _append_checklist(self, lines: list[str], report: Report) -> None:
        items = self._items((report.comparison or {}).get("checklist"))
        lines.extend(["## 7. 建议人工确认", ""])
        if not items:
            lines.extend(["- [ ] 复核当前问题与推荐案例的根因及措施适用性。", ""])
            return
        lines.extend(f"- [ ] {self._display(item)}" for item in items)
        lines.append("")

    def _append_other_cases(self, lines: list[str], report: Report) -> None:
        cases = report.similar_cases
        lines.extend(["## 8. 其他候选案例", ""])
        if len(cases) <= 1:
            lines.extend(["无其他候选案例。", ""])
            return
        for case in cases[1:]:
            lines.extend([
                f"### {self._heading(case.get('case_id') or '未命名案例')}",
                "",
                f"- 候选判断：{self.DECISION_LABELS.get(self._text(case.get('decision')), self._text(case.get('decision')) or '未知')}",
                f"- 综合相似度：{self._score(case.get('similarity_score'))}",
                f"- 判断置信度：{self._percent(case.get('confidence'))}",
                f"- 组织适用性：{self._display((case.get('context_applicability') or {}).get('level')) or '未知'}",
                f"- 推荐等级：{self._display(case.get('recommendation_level')) or '未评级'}",
                f"- 判断说明：{self._display(case.get('decision_reason')) or '未提供'}",
            ])
            self._append_score_table(lines, case.get("dimension_scores"))
            self._append_confidence_details(lines, case.get("confidence_details"))
            self._append_context_applicability(lines, case.get("context_applicability"))
            self._append_list(lines, "推荐依据", case.get("recommendation_reasons"))
            self._append_list(lines, "关键差异", case.get("key_differences"))
            self._append_list(lines, "待验证项", case.get("validation_required"))

    def _append_risks(self, lines: list[str], report: Report) -> None:
        risks = self._items(((report.comparison or {}).get("solution_compare") or {}).get("reuse_risks"))
        for case in report.similar_cases:
            risks.extend(self._items(case.get("risks")))
        risks = self._unique(risks)
        lines.extend(["## 9. 风险提示", ""])
        if not risks:
            lines.extend(["- 当前问题根因与历史措施尚需人工确认，禁止仅凭相似度直接认定重复或照搬措施。", ""])
        else:
            lines.extend(f"- {self._display(item)}" for item in risks)
            lines.extend(["- 最终是否重复、是否复用历史措施，必须由人工确认。", ""])

    def _append_traceability(self, lines: list[str], report: Report) -> None:
        trace = report.traceability
        if not trace:
            return
        lines.extend(["## 10. 追溯信息", "", "| 项目 | 内容 |", "|---|---|"])
        lines.extend([
            f"| 来源交付件 | {self._cell(trace.get('source_artifact'))} |",
            f"| 查询编号 | {self._cell(trace.get('query_id'))} |",
            f"| 候选案例 | {self._cell('、'.join(self._items(trace.get('candidate_case_ids'))))} |",
            "",
        ])


    def _append_confidence_details(self, lines: list[str], details: Any) -> None:
        if not isinstance(details, Mapping):
            return
        reasons = self._items(details.get("reasons"))
        if not reasons:
            return
        lines.extend(["**判断置信度依据**", ""])
        lines.extend(f"- {self._display(item)}" for item in reasons)
        lines.append("")

    def _append_context_applicability(self, lines: list[str], context: Any) -> None:
        if not isinstance(context, Mapping):
            return
        lines.extend(["**组织适用性（仅用于筛选，不参与相似度评分）**", ""])
        lines.extend([
            f"- 适用性：{self._display(context.get('level')) or '未知'}",
            f"- 结论：{self._display(context.get('conclusion')) or '未提供'}",
        ])
        details = context.get("details") or []
        if details:
            lines.extend(["", "| 组织字段 | 当前问题 | 历史案例 | 判断 |", "|---|---|---|---|"])
            for row in details:
                if isinstance(row, Mapping):
                    lines.append(f"| {self._cell(row.get('field'))} | {self._cell(row.get('current'))} | {self._cell(row.get('historical'))} | {self._cell(row.get('status'))} |")
        lines.append("")

    def _append_score_table(self, lines: list[str], scores: Any) -> None:
        if not isinstance(scores, Mapping) or not scores:
            return
        lines.extend(["**相似维度评分**", "", "| 相似维度 | 得分 |", "|---|---:|"])
        for key, value in scores.items():
            label = self.DIMENSION_LABELS.get(str(key), str(key))
            lines.append(f"| {self._cell(label)} | {self._score(value)} |")
        lines.append("")

    @staticmethod
    def _score(value: Any) -> str:
        if value is None or value == "":
            return "未评分"
        try:
            number = float(value)
        except (TypeError, ValueError):
            return "未评分"
        return f"{number:.1f}".rstrip("0").rstrip(".")

    def _append_list(self, lines: list[str], title: str, value: Any) -> None:
        items = self._items(value)
        if not items:
            return
        lines.extend([f"**{title}**", ""])
        lines.extend(f"- {self._display(item)}" for item in items)
        lines.append("")

    @staticmethod
    def _report_from_mapping(value: Mapping[str, Any]) -> Report:
        return Report(
            metadata=dict(value.get("metadata") or {}), summary=dict(value.get("summary") or {}),
            repeat_decision=dict(value.get("repeat_decision") or {}), recommended_case=dict(value.get("recommended_case") or {}),
            comparison=dict(value.get("comparison") or {}),
            similar_cases=[dict(x) for x in value.get("similar_cases") or [] if isinstance(x, Mapping)],
            recommendations=[dict(x) for x in value.get("recommendations") or [] if isinstance(x, Mapping)],
            evidence=[dict(x) for x in value.get("evidence") or [] if isinstance(x, Mapping)],
            traceability=dict(value.get("traceability") or {}), warnings=[dict(x) for x in value.get("warnings") or [] if isinstance(x, Mapping)],
        )

    @staticmethod
    def _items(value: Any) -> list[Any]:
        if value in (None, ""):
            return []
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return list(value)
        return [value]

    @staticmethod
    def _text(value: Any) -> str:
        return "" if value is None else str(value).strip()

    @classmethod
    def _display(cls, value: Any) -> str:
        if value in (None, "", [], {}):
            return ""
        if isinstance(value, Mapping):
            for key in ("value", "normalized", "inferred", "original", "text", "content", "description"):
                candidate = value.get(key)
                if candidate not in (None, "", [], {}):
                    return cls._display(candidate)
            hidden = {"source_type", "confidence", "source", "metadata", "raw", "original", "normalized", "inferred"}
            labels = {
                "corrective_action": "纠正措施", "corrective_actions": "纠正措施",
                "preventive_action": "预防措施", "preventive_actions": "预防措施",
                "solution_object": "措施对象", "solution_mechanism": "措施机制",
                "effective_source": "有效性来源", "expected_effect": "预期效果",
                "reusable_actions": "可复用措施", "recommended_actions": "建议措施",
                "adaptation_required": "适配要求", "reuse_risks": "复用风险",
            }
            parts = []
            for key, item in value.items():
                if str(key) in hidden or item in (None, "", [], {}):
                    continue
                text = cls._display(item)
                if text:
                    parts.append(f"{labels.get(str(key), str(key))}：{text}")
            return "；".join(parts)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return "；".join(text for item in value if (text := cls._display(item)))
        return cls._text(value)

    @classmethod
    def _cell(cls, value: Any) -> str:
        return (cls._display(value) or "未提供").replace("|", "\\|").replace("\n", "<br>")

    @classmethod
    def _heading(cls, value: Any) -> str:
        return cls._text(value).replace("#", "\\#")

    @classmethod
    def _enum(cls, value: Any) -> str:
        text = cls._text(value)
        return cls.ENUM_LABELS.get(text, text)

    @staticmethod
    def _percent(value: Any) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return "未评分"
        if 0 <= number <= 1:
            number *= 100
        return f"{number:.1f}%".replace(".0%", "%")

    @staticmethod
    def _unique(items: Sequence[Any]) -> list[Any]:
        result, seen = [], set()
        for item in items:
            key = str(item).strip()
            if key and key not in seen:
                seen.add(key); result.append(item)
        return result
