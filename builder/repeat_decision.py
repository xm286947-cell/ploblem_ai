from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
import hashlib
import json

import yaml

from pydantic import BaseModel, ConfigDict, Field

from builder.ai_client import AIClientError, MockAIClient
from builder.validators import validate_json
from builder.json_response import parse_json_object, save_raw_response
from builder.similarity_score import calculate_similarity_score, dimension_scores
from builder.confidence_calculator import calculate_confidence
from builder.context_filter import evaluate_context
from quality_knowledge.runtime_model_config import resolve_major_runtime_model_config
from runtime import (
    AgentConfigLoader,
    AgentRequest,
    ConfiguredAgentRuntime,
    RuntimeStatus,
    SqliteTaskStore,
)

REPEAT_DECISION_VERSION = "M8.4-D1"
DECISIONS = {"REPEAT_CASE", "LIKELY_REPEAT", "RELATED_CASE", "NEW_CASE", "INSUFFICIENT_EVIDENCE"}


def _similarity_confidence(similarity: dict[str, Any] | None) -> float:
    analysis = (similarity or {}).get("analysis") or {}
    explicit = analysis.get("confidence")
    try:
        explicit_value = float(explicit)
    except (TypeError, ValueError):
        explicit_value = 0.0
    if explicit_value > 0:
        return max(0.0, min(1.0, explicit_value))
    overall = analysis.get("overall_score")
    try:
        overall_value = float(overall)
    except (TypeError, ValueError):
        overall_value = 0.0
    if overall_value > 0:
        return max(0.0, min(1.0, overall_value / 100.0))
    scores: list[float] = []
    for dimension in (analysis.get("dimensions") or {}).values():
        if not isinstance(dimension, dict):
            continue
        try:
            score = float(dimension.get("score"))
        except (TypeError, ValueError):
            continue
        if score >= 0:
            scores.append(score)
    return round(sum(scores) / len(scores) / 100.0, 4) if scores else 0.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_json(text: str) -> tuple[dict[str, Any], bool]:
    return parse_json_object(text, allow_repair=True)


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def _empty_decision(reason: str = "证据不足，未执行AI判定") -> dict[str, Any]:
    return {
        "decision": "INSUFFICIENT_EVIDENCE",
        "confidence": 0.0,
        "decision_reason": reason,
        "evidence_chain": [],
        "key_differences": [],
        "validation_required": ["补充新问题与历史案例的关键证据"],
        "risks": [],
        "recommended_actions": [],
    }


class RepeatEvidenceDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: str
    strength: Literal["STRONG", "MEDIUM", "WEAK"]
    query_evidence: list[str] = Field(default_factory=list)
    case_evidence: list[str] = Field(default_factory=list)
    reason: str = ""


class RepeatDecisionDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal[
        "REPEAT_CASE",
        "LIKELY_REPEAT",
        "RELATED_CASE",
        "NEW_CASE",
        "INSUFFICIENT_EVIDENCE",
    ]
    confidence: float = Field(ge=0.0, le=1.0)
    decision_reason: str = ""
    evidence_chain: list[RepeatEvidenceDTO] = Field(default_factory=list)
    key_differences: list[str] = Field(default_factory=list)
    validation_required: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)


class RepeatRuntimeDecisionError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        provider_calls: int = 0,
        runtime_status: str = "",
    ) -> None:
        self.code = code
        self.provider_calls = provider_calls
        self.runtime_status = runtime_status
        super().__init__(code)


class RuntimeRepeatDecisionAgent:
    """Runtime-owned provider execution for one Repeat Case candidate."""

    AGENT_ID = "major_issue.repeat_case"
    AGENT_CONFIG = "config/runtime/agents/major_issue.repeat_case.yaml"

    def __init__(
        self,
        root: str | Path,
        *,
        runtime: ConfiguredAgentRuntime,
    ) -> None:
        self.root = Path(root).resolve()
        self.runtime = runtime
        self.resolved = self.runtime.load_agent(self.AGENT_CONFIG)
        self.model_name = str(self.resolved.definition.model or "")

    @classmethod
    def from_project(
        cls,
        root: str | Path,
        *,
        model_config_path: str | Path | None = None,
        runtime_db_path: str | Path | None = None,
    ) -> "RuntimeRepeatDecisionAgent":
        project_root = Path(root).resolve()
        model_config = resolve_major_runtime_model_config(
            project_root,
            model_config_path,
        )
        loader = AgentConfigLoader(
            root=project_root,
            model_profiles=model_config,
            schemas={"RepeatDecisionDTO": RepeatDecisionDTO},
        )
        db_path = (
            Path(runtime_db_path)
            if runtime_db_path is not None
            else project_root / "data/runtime/repeat_decision.sqlite3"
        )
        if not db_path.is_absolute():
            db_path = project_root / db_path
        db_path = db_path.resolve()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        runtime = ConfiguredAgentRuntime(
            SqliteTaskStore(db_path),
            config_loader=loader,
        )
        instance = cls(project_root, runtime=runtime)
        instance.model_config_path = model_config
        return instance

    @staticmethod
    def _request_id(
        query_id: str,
        case_id: str,
        payload: dict[str, Any],
    ) -> str:
        fingerprint = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:24]
        return f"major-repeat:{query_id}:{case_id}:{fingerprint}"

    def decide(
        self,
        payload: dict[str, Any],
        *,
        query_id: str,
        case_id: str,
    ) -> tuple[dict[str, Any], Any]:
        result = self.runtime.invoke(
            AgentRequest(
                request_id=self._request_id(
                    query_id,
                    case_id,
                    payload,
                ),
                agent_id=self.AGENT_ID,
                input=payload,
                metadata={
                    "business_domain": "MAJOR_CASE",
                    "analysis_stage": "repeat_decision",
                    "query_id": query_id,
                    "case_id": case_id,
                    "partition_key": query_id,
                },
            )
        )
        if result.status != RuntimeStatus.COMPLETED:
            error = result.error
            raise RepeatRuntimeDecisionError(
                error.code if error is not None else str(result.status),
                provider_calls=result.execution.provider_calls,
                runtime_status=str(result.status),
            )
        decision = RepeatDecisionDTO.model_validate(
            result.data
        ).model_dump(mode="json")
        return decision, result


class RepeatDecisionEngine:
    """将相似性、解决方案和案例证据综合为候选级判定，并生成查询级统一 RepeatAnalysis。"""

    def __init__(
        self,
        root: str | Path,
        mock: bool = False,
        client: Any | None = None,
        *,
        runtime: ConfiguredAgentRuntime | None = None,
        model_config_path: str | Path | None = None,
        runtime_db_path: str | Path | None = None,
    ) -> None:
        self.root = Path(root)
        model_cfg = yaml.safe_load(
            (self.root / "config/model.yaml").read_text(encoding="utf-8")
        ) or {}
        self.ai_cfg = (
            model_cfg.get("repeat_decision_ai")
            or model_cfg.get("similarity_ai")
            or model_cfg.get("ai")
            or {}
        )
        self.rule_cfg = yaml.safe_load(
            (self.root / "config/repeat_decision.yaml").read_text(
                encoding="utf-8"
            )
        ) or {}
        self.prompt_path = self.root / "prompts/repeat_decision.md"
        self.prompt_template = self.prompt_path.read_text(encoding="utf-8")
        self.prompt_version = str(
            self.ai_cfg.get("prompt_version") or _hash(self.prompt_path)
        )
        self.schema_path = self.root / "schema/repeat_analysis.schema.json"
        self.mock = mock
        self.client_provided = client is not None
        self.client = None
        self.runtime_agent: RuntimeRepeatDecisionAgent | None = None

        if client is not None:
            self.client = client
        elif mock:
            self.client = MockAIClient(
                self.root / "tests/samples/mock_repeat_decision_response.json"
            )
        elif runtime is not None:
            # Explicit Runtime injection is an execution-time opt-in. It is
            # used by controlled acceptance/product wiring without changing
            # the repository default repeat_decision_ai.enabled=false.
            self.runtime_agent = RuntimeRepeatDecisionAgent(
                self.root,
                runtime=runtime,
            )
            self.prompt_version = self.runtime_agent.resolved.prompt.version
        elif bool(self.ai_cfg.get("enabled", False)):
            self.runtime_agent = RuntimeRepeatDecisionAgent.from_project(
                self.root,
                model_config_path=model_config_path,
                runtime_db_path=runtime_db_path,
            )
            self.prompt_version = self.runtime_agent.resolved.prompt.version

    @staticmethod
    def _payload(
        context: dict[str, Any],
        similarity: dict[str, Any] | None,
        solution: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return {
            "query_id": context.get("query_id"),
            "case_id": context.get("case_id"),
            "query": context.get("query", {}),
            "candidate": context.get("candidate", {}),
            "historical_case": context.get("case", {}),
            "evidence": context.get("evidence", {}),
            "quality": context.get("quality", {}),
            "similarity_analysis": similarity or {},
            "solution_analysis": solution or {},
        }

    def _messages(
        self,
        context: dict[str, Any],
        similarity: dict[str, Any] | None,
        solution: dict[str, Any] | None,
    ) -> list[dict[str, str]]:
        payload = self._payload(context, similarity, solution)
        return [
            {"role": "system", "content": self.prompt_template},
            {
                "role": "user",
                "content": json.dumps(
                    payload,
                    ensure_ascii=False,
                    indent=2,
                ),
            },
        ]

    def decide_candidate(
        self,
        context: dict[str, Any],
        similarity: dict[str, Any] | None,
        solution: dict[str, Any] | None,
        skip_ai: bool = False,
    ) -> tuple[dict[str, Any], str, list[dict[str, str]]]:
        warnings: list[dict[str, str]] = []
        if similarity is None:
            warnings.append(
                {
                    "code": "SIMILARITY_ANALYSIS_MISSING",
                    "message": "缺少M8.2相似性分析",
                }
            )
        if solution is None:
            warnings.append(
                {
                    "code": "SOLUTION_ANALYSIS_MISSING",
                    "message": "缺少M8.3解决方案分析",
                }
            )

        ai_available = bool(
            self.runtime_agent is not None
            or self.mock
            or self.client_provided
        )
        if skip_ai or not ai_available:
            return (
                _empty_decision(),
                "SKIPPED",
                warnings
                + [
                    {
                        "code": "DECISION_AI_SKIPPED",
                        "message": "AI未启用或显式跳过",
                    }
                ],
            )

        query_id = str(context.get("query_id") or "")
        case_id = str(context.get("case_id") or "")
        payload = self._payload(context, similarity, solution)

        if self.runtime_agent is not None:
            try:
                decision, result = self.runtime_agent.decide(
                    payload,
                    query_id=query_id,
                    case_id=case_id,
                )
                save_raw_response(
                    self.root,
                    "repeat_decision",
                    query_id,
                    case_id,
                    json.dumps(
                        decision,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    max(1, result.execution.provider_calls),
                )
                return decision, "SUCCESS", warnings
            except (
                RepeatRuntimeDecisionError,
                ValueError,
                RuntimeError,
            ) as exc:
                return (
                    _empty_decision(str(exc)),
                    "FAILED",
                    warnings
                    + [
                        {
                            "code": "REPEAT_DECISION_FAILED",
                            "message": str(exc),
                        }
                    ],
                )

        # Mock/custom-client compatibility only. Production provider execution
        # is Runtime-owned and does not use business-side retry or repair.
        try:
            response = self.client.complete(
                self._messages(context, similarity, solution)
            )
            save_raw_response(
                self.root,
                "repeat_decision",
                query_id,
                case_id,
                response.content,
                1,
            )
            decision, repaired = _extract_json(response.content)
            if decision.get("decision") not in DECISIONS:
                raise ValueError(
                    f"非法decision: {decision.get('decision')}"
                )
            if repaired:
                warnings.append(
                    {
                        "code": "AI_JSON_REPAIRED",
                        "message": (
                            "Mock/legacy client输出存在常见JSON格式错误，"
                            "已做兼容修复"
                        ),
                    }
                )
            return decision, "SUCCESS", warnings
        except (
            AIClientError,
            ValueError,
            json.JSONDecodeError,
            RuntimeError,
        ) as exc:
            return (
                _empty_decision(str(exc)),
                "FAILED",
                warnings
                + [
                    {
                        "code": "REPEAT_DECISION_FAILED",
                        "message": str(exc),
                    }
                ],
            )

    def _score(self, similarity_score: float | None, confidence: float, solution: dict[str, Any] | None, context: dict[str, Any]) -> float:
        """候选排序分：相似度、判断置信度、措施适用性、证据完整度。

        组织上下文只做筛选/适用性展示，不计入分值。
        """
        weights = self.rule_cfg.get("weights", {})
        sim = float(similarity_score or 0.0)
        conf = max(0.0, min(1.0, float(confidence or 0.0))) * 100
        applicability = str(((solution or {}).get("analysis") or {}).get("applicability") or "UNKNOWN")
        app_scores = self.rule_cfg.get("solution_applicability_scores", {})
        app = float(app_scores.get(applicability, 0))
        quality_status = str((context.get("quality") or {}).get("status") or "PARTIAL")
        completeness = 100.0 if quality_status == "COMPLETE" else 60.0 if quality_status == "PARTIAL" else 20.0
        score = (
            sim * float(weights.get("similarity", 0.55))
            + conf * float(weights.get("decision_confidence", 0.25))
            + app * float(weights.get("solution_applicability", 0.10))
            + completeness * float(weights.get("evidence_completeness", 0.10))
        )
        return round(max(0.0, min(100.0, score)), 2)

    @staticmethod
    def _recommendation_level(similarity_score: float | None, confidence: float, context_level: Any) -> str:
        sim = float(similarity_score or 0.0)
        conf = float(confidence or 0.0) * 100
        if sim >= 90 and conf >= 90:
            return "★★★★★"
        if sim >= 80 and conf >= 80:
            return "★★★★☆"
        if sim >= 70 and conf >= 70:
            return "★★★☆☆"
        if sim >= 60 and conf >= 60:
            return "★★☆☆☆"
        return "★☆☆☆☆"

    @staticmethod
    def _recommendation_reasons(similarity: dict[str, Any] | None, context_applicability: dict[str, Any]) -> list[str]:
        analysis = (similarity or {}).get("analysis") or {}
        reasons = [str(x) for x in (analysis.get("key_similarities") or []) if str(x).strip()]
        conclusion = str(context_applicability.get("conclusion") or "").strip()
        if conclusion:
            reasons.append(conclusion)
        return reasons[:6]

    def build_analysis(self, query_id: str, items: list[tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]],
                       skip_ai: bool = False) -> dict[str, Any]:
        candidates: list[dict[str, Any]] = []
        warnings: list[dict[str, str]] = []
        statuses: list[str] = []
        model_name = (
            self.runtime_agent.model_name
            if self.runtime_agent is not None
            else str(
                getattr(self.client, "model", None)
                or self.ai_cfg.get("model")
                or ""
            )
        )
        for context, similarity, solution in items:
            decision, status, item_warnings = self.decide_candidate(context, similarity, solution, skip_ai=skip_ai)
            statuses.append(status)
            warnings.extend({"code": w["code"], "message": f"{context.get('case_id')}: {w['message']}"} for w in item_warnings)
            similarity_score = calculate_similarity_score(similarity)
            scores = dimension_scores(similarity)
            confidence_details = calculate_confidence(
                similarity, context, decision.get("confidence")
            )
            effective_confidence = float(confidence_details["score"])
            decision["confidence"] = effective_confidence
            context_applicability = evaluate_context({
                "query": context.get("query") or {},
                "case": context.get("case") or {},
            })
            final_score = self._score(similarity_score, effective_confidence, solution, context)
            recommendation_level = self._recommendation_level(similarity_score, effective_confidence, context_applicability.get("level"))
            candidates.append({
                "case_id": str(context.get("case_id") or ""),
                "retrieval_rank": int((context.get("candidate") or {}).get("rank") or 0),
                "final_rank": 0,
                "final_score": final_score,
                "decision": str(decision.get("decision") or "INSUFFICIENT_EVIDENCE"),
                "confidence": effective_confidence,
                "confidence_details": confidence_details,
                "similarity_score": similarity_score,
                "dimension_scores": scores,
                "context_applicability": context_applicability,
                "recommendation_level": recommendation_level,
                "recommendation_reasons": self._recommendation_reasons(similarity, context_applicability),
                "decision_reason": str(decision.get("decision_reason") or ""),
                "evidence_chain": list(decision.get("evidence_chain") or []),
                "key_differences": list(decision.get("key_differences") or []),
                "validation_required": list(decision.get("validation_required") or []),
                "risks": list(decision.get("risks") or []),
                "recommended_actions": list(decision.get("recommended_actions") or []),
                "similarity": similarity or {},
                "solution": solution or {},
                "comparison_context": {
                    "query": context.get("query") or {},
                    "case": context.get("case") or {},
                    "evidence": context.get("evidence") or {},
                    "quality": context.get("quality") or {},
                },
            })
        priority = self.rule_cfg.get("decision_priority", {})
        candidates.sort(key=lambda x: (int(priority.get(x["decision"], 0)), x["final_score"], x["confidence"]), reverse=True)
        for idx, candidate in enumerate(candidates, 1):
            candidate["final_rank"] = idx
        if candidates:
            best = candidates[0]
            best_case = {k: best[k] for k in ["case_id", "final_rank", "final_score", "decision", "confidence", "decision_reason"]}
            final_decision = best["decision"]
            overall_confidence = best["confidence"]
        else:
            best_case = {"case_id": "", "final_rank": 0, "final_score": 0.0, "decision": "INSUFFICIENT_EVIDENCE", "confidence": 0.0, "decision_reason": "无候选案例"}
            final_decision = "INSUFFICIENT_EVIDENCE"
            overall_confidence = 0.0
            warnings.append({"code": "NO_CANDIDATES", "message": "未找到可判定的候选案例"})
        if statuses and all(s == "SUCCESS" for s in statuses):
            analysis_status = "SUCCESS"
        elif statuses and any(s == "SUCCESS" for s in statuses):
            analysis_status = "PARTIAL_SUCCESS"
        elif statuses and all(s == "SKIPPED" for s in statuses):
            analysis_status = "SKIPPED"
        else:
            analysis_status = "FAILED" if statuses else "SKIPPED"
        result = {
            "metadata": {
                "query_id": query_id,
                "decision_version": REPEAT_DECISION_VERSION,
                "prompt_version": self.prompt_version,
                "model_provider": str(self.ai_cfg.get("provider", "openai_compatible")),
                "model_name": model_name,
                "generated_at": _now(),
            },
            "final_decision": final_decision,
            "overall_confidence": overall_confidence,
            "best_case": best_case,
            "candidates": candidates,
            "analysis_status": analysis_status,
            "warnings": warnings,
        }
        errors = validate_json(result, self.schema_path)
        if errors:
            raise ValueError("; ".join(errors))
        return result
