from __future__ import annotations

from typing import Any, Mapping, Sequence

from presentation.contract.analysis_result import AnalysisResult
from presentation.contract.report import Report
from presentation.model.comparison_result import ComparisonResult
from builder.similarity_score import calculate_similarity_score, dimension_scores
from builder.confidence_calculator import calculate_confidence
from builder.context_filter import evaluate_context


class ReportBuilder:
    """Build a detailed human-review report without changing analysis decisions."""

    CONTRACT_NAME = "QUALITY_AGENT_PRESENTATION_REPORT"
    CONTRACT_VERSION = "2.1"

    REASON_ALIASES = {
        "原因一级分类": ("原因一级分类", "一级原因分类", "cause_level1", "cause_level_1", "reason_level_1", "level1_reason"),
        "原因二级分类": ("原因二级分类", "二级原因分类", "cause_level2", "cause_level_2", "reason_level_2", "level2_reason"),
        "原因三级分类": ("原因三级分类", "三级原因分类", "cause_level3", "cause_level_3", "reason_level_3", "level3_reason"),
        "原因四级分类": ("原因四级分类", "四级原因分类", "cause_level4", "cause_level_4", "reason_level_4", "level4_reason"),
    }
    ROOT_CAUSE_ALIASES = ("问题根因", "根因", "技术根因", "TRC", "trc", "root_cause", "technical_root_cause")
    SOLUTION_ALIASES = ("改进措施", "解决措施", "解决方案", "技术改进措施", "solution", "corrective_action", "corrective_actions")
    MODULE_ALIASES = ("问题模块", "模块", "feature", "module", "problem_module")
    OBJECT_ALIASES = ("故障对象", "问题对象", "problem_object", "failure_object")

    def build(self, analysis: AnalysisResult | Mapping[str, Any]) -> Report:
        result = analysis if isinstance(analysis, AnalysisResult) else AnalysisResult.from_mapping(analysis)
        candidates = [self._case_view(item) for item in result.candidates]
        best_raw = self._best_candidate(result)
        overall_confidence = result.overall_confidence or self._candidate_confidence(best_raw)
        comparison = self._build_comparison(best_raw) if best_raw else ComparisonResult()

        metadata = dict(result.metadata)
        metadata.update({"contract_name": self.CONTRACT_NAME, "contract_version": self.CONTRACT_VERSION})
        return Report(
            metadata=metadata,
            summary={
                "query_id": result.query_id,
                "analysis_status": result.analysis_status,
                "candidate_count": len(result.candidates),
                "warning_count": len(result.warnings),
                "human_confirmation_required": True,
            },
            repeat_decision={
                "decision": result.final_decision,
                "confidence": overall_confidence,
                "best_case": dict(result.best_case),
                "notice": "AI初步判断，仅用于辅助分析，最终是否重复需人工确认。",
            },
            recommended_case=self._recommended_case(best_raw),
            comparison=comparison.to_dict(),
            similar_cases=candidates,
            recommendations=self._collect_recommendations(result.candidates),
            evidence=self._collect_evidence(result.candidates),
            traceability={
                "source_artifact": "repeat_analysis.json",
                "query_id": result.query_id,
                "candidate_case_ids": [str(item.get("case_id") or "") for item in result.candidates],
            },
            warnings=[dict(item) for item in result.warnings],
        )

    @staticmethod
    def _best_candidate(result: AnalysisResult) -> dict[str, Any]:
        best_id = str(result.best_case.get("case_id") or "")
        for item in result.candidates:
            if str(item.get("case_id") or "") == best_id:
                return item
        return result.candidates[0] if result.candidates else {}

    def _recommended_case(self, candidate: Mapping[str, Any]) -> dict[str, Any]:
        if not candidate:
            return {}
        solution = self._analysis_payload(candidate.get("solution"))
        similarity = self._analysis_payload(candidate.get("similarity"))
        dimensions = similarity.get("dimensions") or {}
        return {
            "case_id": str(candidate.get("case_id") or ""),
            "decision": str(candidate.get("decision") or "INSUFFICIENT_EVIDENCE"),
            "confidence": self._candidate_confidence(candidate),
            "confidence_details": dict(candidate.get("confidence_details") or calculate_confidence(candidate.get("similarity"), candidate.get("comparison_context") or {}, candidate.get("confidence"))),
            "similarity_score": candidate.get("similarity_score") if candidate.get("similarity_score") is not None else calculate_similarity_score(candidate.get("similarity")),
            "dimension_scores": dict(candidate.get("dimension_scores") or dimension_scores(candidate.get("similarity"))),
            "context_applicability": dict(candidate.get("context_applicability") or evaluate_context(candidate.get("comparison_context") or {})),
            "recommendation_level": str(candidate.get("recommendation_level") or self._recommendation_level(
                candidate.get("similarity_score") if candidate.get("similarity_score") is not None else calculate_similarity_score(candidate.get("similarity")),
                self._candidate_confidence(candidate),
            )),
            "recommendation_reasons": self._items(candidate.get("recommendation_reasons")),
            "decision_reason": str(candidate.get("decision_reason") or ""),
            "historical_solution_summary": str(solution.get("historical_solution_summary") or ""),
            "closure_status": str(solution.get("closure_status") or ""),
            "effectiveness": str(solution.get("effectiveness") or ""),
            "applicability": str(solution.get("applicability") or ""),
        }

    def _build_comparison(self, candidate: Mapping[str, Any]) -> ComparisonResult:
        context = candidate.get("comparison_context") or {}
        query = ((context.get("query") or {}).get("standard_query") or context.get("query") or {})
        case_root = context.get("case") or {}
        case = case_root.get("enriched_case") or case_root.get("standard_case") or case_root
        similarity = self._analysis_payload(candidate.get("similarity"))
        solution = self._analysis_payload(candidate.get("solution"))

        reason_rows: list[dict[str, Any]] = []
        for label, aliases in self.REASON_ALIASES.items():
            current = self._find_value(query, aliases)
            historical = self._find_value(case, aliases)
            status = self._compare(current, historical)
            reason_rows.append({"dimension": label, "current": current, "historical": historical, "status": status})

        module_current = self._find_value(query, self.MODULE_ALIASES)
        module_history = self._find_value(case, self.MODULE_ALIASES)
        if module_current or module_history:
            reason_rows.append({"dimension": "问题模块", "current": module_current, "historical": module_history, "status": self._compare(module_current, module_history)})
        object_current = self._find_value(query, self.OBJECT_ALIASES)
        object_history = self._find_value(case, self.OBJECT_ALIASES)
        if object_current or object_history:
            reason_rows.append({"dimension": "故障对象", "current": object_current, "historical": object_history, "status": self._compare(object_current, object_history)})

        root_dimension = (similarity.get("dimensions") or {}).get("root_cause") or (similarity.get("dimensions") or {}).get("trc") or {}
        current_root = self._find_value(query, self.ROOT_CAUSE_ALIASES) or self._join(root_dimension.get("query_evidence"))
        historical_root = self._find_value(case, self.ROOT_CAUSE_ALIASES) or self._join(root_dimension.get("case_evidence"))
        root_status = self._similarity_status(root_dimension, current_root, historical_root)
        root_reason = str(root_dimension.get("reason") or candidate.get("decision_reason") or "")
        root_compare = {
            "current": current_root,
            "historical": historical_root,
            "status": root_status,
            "reason": root_reason,
            "common_points": self._items(root_dimension.get("common_points") or root_dimension.get("similarities")),
            "difference_points": self._items(root_dimension.get("difference_points") or root_dimension.get("differences")),
            "analysis_conclusion": self._root_conclusion(current_root, historical_root, root_status, root_reason),
            "evidence_status": "待确认" if not current_root or not historical_root else "已有证据",
        }

        current_solution = self._find_value(query, self.SOLUTION_ALIASES)
        historical_solution = self._find_value(case, self.SOLUTION_ALIASES) or str(solution.get("historical_solution_summary") or "")
        reusable = self._items(solution.get("reusable_actions"))
        risks = self._items(solution.get("reuse_risks")) + self._items(candidate.get("risks"))
        corrective_actions = self._items(solution.get("corrective_actions") or solution.get("corrective_action"))
        preventive_actions = self._items(solution.get("preventive_actions") or solution.get("preventive_action"))
        recommended_actions = self._items(candidate.get("recommended_actions"))
        adaptation_required = self._items(solution.get("adaptation_required"))
        common_actions, different_actions = self._action_comparison(
            current_solution,
            historical_solution,
            corrective_actions,
            preventive_actions,
            reusable,
        )
        analysis_summary = str(solution.get("analysis_summary") or "")
        solution_compare = {
            "current": current_solution,
            "historical": historical_solution,
            "corrective_actions": corrective_actions,
            "preventive_actions": preventive_actions,
            "common_actions": common_actions,
            "different_actions": different_actions,
            "reusable_actions": reusable,
            "recommended_actions": recommended_actions,
            "adaptation_required": adaptation_required,
            "reuse_risks": self._unique(risks),
            "applicability": str(solution.get("applicability") or "UNKNOWN"),
            "effectiveness": str(solution.get("effectiveness") or "UNKNOWN"),
            "analysis_summary": analysis_summary,
            "analysis_conclusion": self._solution_conclusion(
                current_solution,
                historical_solution,
                corrective_actions,
                preventive_actions,
                reusable,
                recommended_actions,
                adaptation_required,
                analysis_summary,
            ),
        }

        reasons = self._recommendation_reasons(reason_rows, root_compare, similarity)
        checklist = self._checklist(reason_rows, root_compare, solution_compare, candidate)
        return ComparisonResult(reason_rows, root_compare, solution_compare, reasons, checklist)

    @classmethod
    def _root_conclusion(cls, current: str, historical: str, status: str, reason: str) -> str:
        if reason:
            return reason
        if not current or not historical:
            return "当前问题或历史案例根因信息不完整，暂不能形成可靠的根因对比结论。"
        if status in {"一致", "高度相似", "相似"}:
            return "当前问题与历史案例的根因方向相近，可作为重复问题判断的重要证据，但仍需结合触发条件和故障对象人工确认。"
        return "当前问题与历史案例的根因存在差异，不建议仅依据现象相似认定为重复问题。"

    @classmethod
    def _action_comparison(
        cls,
        current: str,
        historical: str,
        corrective: Sequence[Any],
        preventive: Sequence[Any],
        reusable: Sequence[Any],
    ) -> tuple[list[str], list[str]]:
        current_key = cls._key(current) if current else ""
        history_items = [historical, *corrective, *preventive, *reusable]
        common: list[str] = []
        different: list[str] = []
        for item in history_items:
            text = cls._join(item)
            if not text:
                continue
            item_key = cls._key(text)
            if current_key and (current_key in item_key or item_key in current_key):
                common.append(text)
            else:
                different.append(text)
        return cls._unique(common), cls._unique(different)

    @classmethod
    def _solution_conclusion(
        cls,
        current: str,
        historical: str,
        corrective: Sequence[Any],
        preventive: Sequence[Any],
        reusable: Sequence[Any],
        recommended: Sequence[Any],
        adaptation: Sequence[Any],
        summary: str,
    ) -> str:
        if summary:
            return summary
        if not any((current, historical, corrective, preventive, reusable, recommended, adaptation)):
            return "当前问题与历史案例均缺少可用于对比的措施信息。"
        if preventive:
            return "历史案例除纠正措施外还包含预防措施。复用前应确认当前问题根因和适用条件，并评估是否需要同步补充测试、设计约束或过程防复发措施。"
        if reusable or recommended or adaptation:
            return "历史措施具备一定参考价值，但需结合当前问题的根因、对象和适用条件进行裁剪后再使用。"
        return "当前措施与历史措施已完成并列展示，是否可复用仍需结合根因一致性和实施效果人工确认。"

    @classmethod
    def _case_view(cls, candidate: Mapping[str, Any]) -> dict[str, Any]:
        similarity_score = candidate.get("similarity_score") if candidate.get("similarity_score") is not None else calculate_similarity_score(candidate.get("similarity"))
        confidence = cls._candidate_confidence(candidate)
        scores = dict(candidate.get("dimension_scores") or dimension_scores(candidate.get("similarity")))
        return {
            "case_id": str(candidate.get("case_id") or ""),
            "decision": str(candidate.get("decision") or "INSUFFICIENT_EVIDENCE"),
            "confidence": confidence,
            "confidence_details": dict(candidate.get("confidence_details") or calculate_confidence(candidate.get("similarity"), candidate.get("comparison_context") or {}, candidate.get("confidence"))),
            "similarity_score": similarity_score,
            "dimension_scores": scores,
            "context_applicability": dict(candidate.get("context_applicability") or evaluate_context(candidate.get("comparison_context") or {})),
            "recommendation_level": str(candidate.get("recommendation_level") or cls._recommendation_level(similarity_score, confidence)),
            "recommendation_reasons": cls._items(candidate.get("recommendation_reasons")),
            "decision_reason": str(candidate.get("decision_reason") or ""),
            "key_differences": list(candidate.get("key_differences") or []),
            "validation_required": list(candidate.get("validation_required") or []),
            "risks": list(candidate.get("risks") or []),
        }

    @staticmethod
    def _recommendation_level(similarity_score: Any, confidence: Any) -> str:
        try:
            sim = float(similarity_score or 0.0)
            conf = float(confidence or 0.0) * 100
        except (TypeError, ValueError):
            return "★☆☆☆☆"
        if sim >= 90 and conf >= 90:
            return "★★★★★"
        if sim >= 80 and conf >= 80:
            return "★★★★☆"
        if sim >= 70 and conf >= 70:
            return "★★★☆☆"
        if sim >= 60 and conf >= 60:
            return "★★☆☆☆"
        return "★☆☆☆☆"

    @classmethod
    def _candidate_confidence(cls, candidate: Mapping[str, Any]) -> float:
        if not candidate:
            return 0.0
        try:
            explicit = float(candidate.get("confidence") or 0.0)
        except (TypeError, ValueError):
            explicit = 0.0
        if explicit > 0:
            return max(0.0, min(1.0, explicit if explicit <= 1 else explicit / 100.0))
        details = calculate_confidence(candidate.get("similarity"), candidate.get("comparison_context") or {}, None)
        return float(details.get("score") or 0.0)

    @staticmethod
    def _overall_similarity_score(similarity: Mapping[str, Any]) -> float | None:
        return calculate_similarity_score(similarity)

    def _recommendation_reasons(self, rows: Sequence[Mapping[str, Any]], root: Mapping[str, Any], similarity: Mapping[str, Any]) -> list[str]:
        reasons = [f"{row['dimension']}一致" for row in rows if row.get("status") == "一致"]
        if root.get("status") in {"一致", "高度相似", "相似"}:
            reasons.append("问题根因或技术根因方向相近")
        for item in self._items(similarity.get("key_similarities")):
            reasons.append(str(item))
        return self._unique(reasons)[:8]

    def _checklist(self, rows: Sequence[Mapping[str, Any]], root: Mapping[str, Any], solution: Mapping[str, Any], candidate: Mapping[str, Any]) -> list[str]:
        items: list[str] = []
        for row in rows:
            if row.get("status") in {"不一致", "缺失"}:
                items.append(f"确认{row.get('dimension')}是否填写正确，并核实两案是否一致")
        if root.get("evidence_status") == "待确认" or root.get("status") in {"不一致", "缺失", "待确认"}:
            items.append("完成当前问题TRC/问题根因确认，并与历史案例逐项对比")
        items.extend(str(item) for item in self._items(candidate.get("validation_required")))
        if solution.get("reusable_actions") or solution.get("historical"):
            items.append("确认历史措施的适用条件，禁止在根因未确认前直接复用")
        if not items:
            items.append("复核当前问题与历史案例的根因、触发条件和措施适用性")
        return self._unique(items)

    @staticmethod
    def _analysis_payload(value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            return {}
        nested = value.get("analysis")
        return dict(nested) if isinstance(nested, Mapping) else dict(value)

    @classmethod
    def _find_value(cls, value: Any, aliases: Sequence[str]) -> str:
        normalized = {cls._key(alias) for alias in aliases}
        if isinstance(value, Mapping):
            for key, item in value.items():
                if cls._key(key) in normalized and item not in (None, "", [], {}):
                    return cls._join(item)
            for item in value.values():
                found = cls._find_value(item, aliases)
                if found:
                    return found
        elif isinstance(value, list):
            for item in value:
                found = cls._find_value(item, aliases)
                if found:
                    return found
        return ""

    @staticmethod
    def _key(value: Any) -> str:
        return "".join(str(value).lower().split()).replace("-", "").replace("_", "")

    @classmethod
    def _join(cls, value: Any) -> str:
        if value in (None, "", [], {}):
            return ""
        if isinstance(value, Mapping):
            # Evidence/value wrappers are internal transport objects. Only expose
            # the business value in Report JSON and Markdown.
            for key in ("effective", "value", "normalized", "inferred", "original", "text", "content", "description"):
                candidate = value.get(key)
                if candidate not in (None, "", [], {}):
                    return cls._join(candidate)
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
                text = cls._join(item)
                if text:
                    parts.append(f"{labels.get(str(key), str(key))}：{text}")
            return "；".join(parts)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return "；".join(text for item in value if (text := cls._join(item)))
        return str(value).strip()

    @classmethod
    def _compare(cls, current: str, historical: str) -> str:
        if not current or not historical:
            return "缺失"
        return "一致" if cls._key(current) == cls._key(historical) else "不一致"

    @classmethod
    def _similarity_status(cls, dimension: Mapping[str, Any], current: str, historical: str) -> str:
        assessment = str(dimension.get("assessment") or "")
        if assessment == "HIGHLY_SIMILAR":
            return "高度相似"
        if assessment == "SIMILAR":
            return "相似"
        if assessment in {"DIFFERENT", "NOT_SIMILAR"}:
            return "不一致"
        return cls._compare(current, historical) if current or historical else "待确认"

    @staticmethod
    def _items(value: Any) -> list[Any]:
        if value in (None, ""):
            return []
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return list(value)
        return [value]

    @staticmethod
    def _unique(items: Sequence[Any]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for item in items:
            text = str(item).strip()
            if text and text not in seen:
                seen.add(text)
                result.append(text)
        return result

    @staticmethod
    def _collect_recommendations(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{"case_id": str(c.get("case_id") or ""), "action": a} for c in candidates for a in (c.get("recommended_actions") or [])]

    @staticmethod
    def _collect_evidence(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{"case_id": str(c.get("case_id") or ""), "evidence": e} for c in candidates for e in (c.get("evidence_chain") or [])]
