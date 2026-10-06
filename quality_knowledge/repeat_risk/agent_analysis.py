from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Literal
import hashlib
import json

import yaml
from pydantic import BaseModel, ConfigDict, Field

from presentation.delivery_service import DeliveryService
from quality_knowledge.runtime_model_config import resolve_major_runtime_model_config
from runtime import (
    AgentConfigLoader,
    AgentRequest,
    ConfiguredAgentRuntime,
    RuntimeStatus,
    SqliteTaskStore,
)


SIMILARITY_AGENT_ID = "major_issue.repeat_similarity"
SIMILARITY_AGENT_CONFIG = "config/runtime/agents/major_issue.repeat_similarity.yaml"
SOLUTION_AGENT_ID = "major_issue.repeat_solution"
SOLUTION_AGENT_CONFIG = "config/runtime/agents/major_issue.repeat_solution.yaml"
DECISION_AGENT_ID = "major_issue.repeat_case"
DECISION_AGENT_CONFIG = "config/runtime/agents/major_issue.repeat_case.yaml"


class RepeatSimilarityDimensionDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: int = Field(ge=0, le=100)
    assessment: Literal[
        "HIGHLY_SIMILAR",
        "SIMILAR",
        "PARTIALLY_RELATED",
        "WEAKLY_RELATED",
        "NOT_SIMILAR",
        "UNKNOWN",
    ]
    query_evidence: list[str] = Field(default_factory=list)
    case_evidence: list[str] = Field(default_factory=list)
    reason: str = ""


class RepeatSimilarityDimensionsDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    problem_object: RepeatSimilarityDimensionDTO
    phenomenon: RepeatSimilarityDimensionDTO
    trigger_condition: RepeatSimilarityDimensionDTO
    impact: RepeatSimilarityDimensionDTO
    failure_mechanism: RepeatSimilarityDimensionDTO
    trc: RepeatSimilarityDimensionDTO
    mrc: RepeatSimilarityDimensionDTO
    root_cause: RepeatSimilarityDimensionDTO
    classification: RepeatSimilarityDimensionDTO
    organization_context: RepeatSimilarityDimensionDTO


class RepeatSimilarityDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimensions: RepeatSimilarityDimensionsDTO
    overall_score: int = Field(ge=0, le=100)
    overall_level: Literal["HIGH", "MEDIUM", "LOW", "UNKNOWN"]
    key_similarities: list[str] = Field(default_factory=list)
    key_differences: list[str] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)
    analysis_summary: str = ""
    confidence: float = Field(ge=0.0, le=1.0)


class RepeatSolutionDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    historical_solution_summary: str = ""
    corrective_actions: list[str] = Field(default_factory=list)
    preventive_actions: list[str] = Field(default_factory=list)
    verification_evidence: list[str] = Field(default_factory=list)
    closure_status: Literal["CLOSED", "PARTIAL", "NOT_CLOSED", "UNKNOWN"]
    effectiveness: Literal[
        "EFFECTIVE",
        "PARTIALLY_EFFECTIVE",
        "INEFFECTIVE",
        "UNKNOWN",
    ]
    applicability: Literal[
        "DIRECT_REUSE",
        "PARTIAL_REUSE",
        "REFERENCE_ONLY",
        "NOT_APPLICABLE",
        "UNKNOWN",
    ]
    reusable_actions: list[str] = Field(default_factory=list)
    adaptation_required: list[str] = Field(default_factory=list)
    reuse_risks: list[str] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)
    analysis_summary: str = ""
    confidence: float = Field(ge=0.0, le=1.0)


class RepeatEvidenceDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: str
    strength: Literal["STRONG", "MEDIUM", "WEAK"]
    query_evidence: list[str] = Field(default_factory=list)
    case_evidence: list[str] = Field(default_factory=list)
    reason: str = ""


class RepeatDecisionDTO(BaseModel):
    """Mature M8.4 output contract used only as a Unified Runtime schema."""

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


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


class RepeatAgentAnalysisService:
    """Restore mature Repeat analysis on top of the current product contracts.

    Retrieval/ranking and Historical Case authority stay outside this service.
    The service consumes only the already-returned candidate projection and
    invokes model providers exclusively through Unified Runtime.
    """

    def __init__(
        self,
        project_root: str | Path,
        *,
        model_config_path: str | Path | None = None,
        runtime: ConfiguredAgentRuntime | None = None,
        report_root: str | Path | None = None,
        decision_enabled: bool | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.model_config_path = model_config_path
        self.runtime = runtime
        self._agents_loaded = False
        self._resolved: dict[str, Any] = {}
        self.report_root = (
            Path(report_root)
            if report_root is not None
            else self.project_root / "data/repeat_reports"
        )
        if not self.report_root.is_absolute():
            self.report_root = self.project_root / self.report_root
        self.report_root = self.report_root.resolve()
        self.decision_enabled = (
            self._legacy_decision_enabled()
            if decision_enabled is None
            else bool(decision_enabled)
        )

    def _legacy_decision_enabled(self) -> bool:
        path = self.project_root / "config/model.yaml"
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            return False
        return bool((raw.get("repeat_decision_ai") or {}).get("enabled", False))

    def _solution_policy(self) -> tuple[int, float]:
        """Reuse the historical M8.3 candidate selection policy unchanged."""

        path = self.project_root / "config/model.yaml"
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            return 3, 0.0
        config = raw.get("solution_ai") or {}
        try:
            top_n = max(0, int(config.get("candidate_top_n", 3)))
        except (TypeError, ValueError):
            top_n = 3
        try:
            min_score = float(config.get("min_similarity_score", 0))
        except (TypeError, ValueError):
            min_score = 0.0
        return top_n, min_score

    def _ensure_runtime(self) -> ConfiguredAgentRuntime:
        if self.runtime is None:
            model_config = resolve_major_runtime_model_config(
                self.project_root,
                self.model_config_path,
            )
            loader = AgentConfigLoader(
                root=self.project_root,
                model_profiles=model_config,
                schemas={
                    "RepeatSimilarityDTO": RepeatSimilarityDTO,
                    "RepeatSolutionDTO": RepeatSolutionDTO,
                    "RepeatDecisionDTO": RepeatDecisionDTO,
                },
            )
            runtime_db = self.project_root / "data/runtime/repeat_analysis.sqlite3"
            runtime_db.parent.mkdir(parents=True, exist_ok=True)
            self.runtime = ConfiguredAgentRuntime(
                SqliteTaskStore(runtime_db),
                config_loader=loader,
            )

        if not self._agents_loaded:
            self._resolved[SIMILARITY_AGENT_ID] = self.runtime.load_agent(
                SIMILARITY_AGENT_CONFIG
            )
            self._resolved[SOLUTION_AGENT_ID] = self.runtime.load_agent(
                SOLUTION_AGENT_CONFIG
            )
            if self.decision_enabled:
                self._resolved[DECISION_AGENT_ID] = self.runtime.load_agent(
                    DECISION_AGENT_CONFIG
                )
            self._agents_loaded = True
        return self.runtime

    @staticmethod
    def _request_id(
        stage: str,
        query_id: str,
        case_id: str,
        payload: dict[str, Any],
    ) -> str:
        return (
            f"major-repeat:{stage}:{query_id}:{case_id}:"
            f"{_hash(payload)[:20]}"
        )

    def _invoke(
        self,
        *,
        agent_id: str,
        stage: str,
        query_id: str,
        case_id: str,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        runtime = self._ensure_runtime()
        result = runtime.invoke(
            AgentRequest(
                request_id=self._request_id(
                    stage,
                    query_id,
                    case_id,
                    payload,
                ),
                agent_id=agent_id,
                input=payload,
                metadata={
                    "business_domain": "MAJOR_CASE",
                    "analysis_stage": stage,
                    "query_id": query_id,
                    "case_id": case_id,
                    "partition_key": query_id,
                },
            )
        )
        if result.status != RuntimeStatus.COMPLETED:
            error = result.error
            return None, {
                "status": "FAILED",
                "code": error.code if error is not None else str(result.status),
                "message": error.message if error is not None else str(result.status),
                "provider_calls": result.execution.provider_calls,
            }
        data = result.data if isinstance(result.data, dict) else {}
        return deepcopy(data), {
            "status": "SUCCESS",
            "provider_calls": result.execution.provider_calls,
            "model": result.execution.model,
            "provider": result.execution.provider,
        }

    @staticmethod
    def _candidate_context(
        query_id: str,
        query_input: dict[str, Any],
        candidate: dict[str, Any],
    ) -> dict[str, Any]:
        typed_semantic = {
            "mode": candidate.get("semantic_mode"),
            "contract_version": candidate.get("semantic_contract_version"),
            "causes": deepcopy(candidate.get("typed_causes") or []),
            "actions": deepcopy(candidate.get("typed_actions") or []),
            "coverage": deepcopy(candidate.get("semantic_coverage") or {}),
            "evidence_status": candidate.get("semantic_evidence_status"),
        }
        return {
            "query_id": query_id,
            "case_id": candidate.get("case_id"),
            "query": deepcopy(query_input),
            "candidate": {
                "case_id": candidate.get("case_id"),
                "rank": candidate.get("rank"),
                "score": candidate.get("retrieval_score"),
                "title": candidate.get("title"),
                "retrieval_reason": deepcopy(candidate.get("retrieval_reason") or []),
                "matched_fields": deepcopy(candidate.get("matched_fields") or []),
            },
            "case": {
                "case_id": candidate.get("case_id"),
                "title": candidate.get("title"),
                "problem_description": candidate.get("problem_description"),
                "historical_phenomenon": candidate.get("historical_phenomenon"),
                "root_causes": deepcopy(candidate.get("root_causes") or []),
                "measures": deepcopy(candidate.get("measures") or []),
                "verification": candidate.get("verification"),
                "typed_semantic": typed_semantic,
            },
            "evidence": {
                "items": deepcopy(candidate.get("evidence") or []),
                "refs": deepcopy(candidate.get("evidence_refs") or []),
                "typed_semantic": typed_semantic,
            },
            "quality": {
                "status": (
                    "COMPLETE"
                    if candidate.get("detail_status") == "SUCCESS"
                    and candidate.get("semantic_evidence_status")
                    not in {"INCOMPLETE"}
                    else "PARTIAL"
                ),
                "detail_status": candidate.get("detail_status"),
                "semantic_evidence_status": candidate.get(
                    "semantic_evidence_status"
                ),
            },
        }

    def _similarity(
        self,
        context: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        query_id = _text(context.get("query_id"))
        case_id = _text(context.get("case_id"))
        analysis, execution = self._invoke(
            agent_id=SIMILARITY_AGENT_ID,
            stage="repeat_similarity",
            query_id=query_id,
            case_id=case_id,
            payload=context,
        )
        status = "SUCCESS" if analysis is not None else "AI_ANALYSIS_FAILED"
        resolved = self._resolved.get(SIMILARITY_AGENT_ID)
        wrapper = {
            "metadata": {
                "query_id": query_id,
                "case_id": case_id,
                "source_analysis_context": "repeat-result/v1",
                "analyzer_version": "M8.2-RUNTIME-RESTORE-1",
                "prompt_version": (
                    resolved.prompt.version if resolved is not None else "M8.2-P1"
                ),
                "model_provider": execution.get("provider") or "",
                "model_name": execution.get("model") or "",
            },
            "retrieval": {
                "rank": int((context.get("candidate") or {}).get("rank") or 0),
                "retrieval_score": float(
                    (context.get("candidate") or {}).get("score") or 0.0
                ),
            },
            "analysis": analysis or {},
            "analysis_status": status,
            "warnings": (
                []
                if analysis is not None
                else [{
                    "code": execution.get("code") or "SIMILARITY_ANALYSIS_FAILED",
                    "message": execution.get("message") or "M8.2 Runtime analysis failed",
                }]
            ),
        }
        return wrapper, execution

    def _solution(
        self,
        context: dict[str, Any],
        similarity: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        query_id = _text(context.get("query_id"))
        case_id = _text(context.get("case_id"))
        payload = {
            **deepcopy(context),
            "similarity_analysis": deepcopy(similarity),
        }
        analysis, execution = self._invoke(
            agent_id=SOLUTION_AGENT_ID,
            stage="repeat_solution",
            query_id=query_id,
            case_id=case_id,
            payload=payload,
        )
        status = "SUCCESS" if analysis is not None else "AI_ANALYSIS_FAILED"
        resolved = self._resolved.get(SOLUTION_AGENT_ID)
        similarity_analysis = similarity.get("analysis") or {}
        wrapper = {
            "metadata": {
                "query_id": query_id,
                "case_id": case_id,
                "source_analysis_context": "repeat-result/v1",
                "source_similarity_analysis": "runtime:repeat_similarity",
                "analyzer_version": "M8.3-RUNTIME-RESTORE-1",
                "prompt_version": (
                    resolved.prompt.version if resolved is not None else "M8.3-P1"
                ),
                "model_provider": execution.get("provider") or "",
                "model_name": execution.get("model") or "",
            },
            "similarity_reference": {
                "overall_score": int(similarity_analysis.get("overall_score") or 0),
                "overall_level": _text(
                    similarity_analysis.get("overall_level")
                ) or "UNKNOWN",
                "analysis_status": similarity.get("analysis_status") or "MISSING",
            },
            "analysis": analysis or {},
            "analysis_status": status,
            "warnings": (
                []
                if analysis is not None
                else [{
                    "code": execution.get("code") or "SOLUTION_ANALYSIS_FAILED",
                    "message": execution.get("message") or "M8.3 Runtime analysis failed",
                }]
            ),
        }
        return wrapper, execution

    def _recommendation(
        self,
        context: dict[str, Any],
        similarity: dict[str, Any],
        solution: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if not self.decision_enabled:
            return {
                "status": "DISABLED",
                "decision": None,
                "confidence": None,
                "decision_reason": "M8.4 AI Recommendation is disabled by configuration.",
                "evidence_chain": [],
                "key_differences": [],
                "validation_required": [],
                "risks": [],
                "recommended_actions": [],
            }, {"status": "DISABLED", "provider_calls": 0}

        payload = {
            **deepcopy(context),
            "similarity_analysis": deepcopy(similarity),
            "solution_analysis": deepcopy(solution),
        }
        query_id = _text(context.get("query_id"))
        case_id = _text(context.get("case_id"))
        decision, execution = self._invoke(
            agent_id=DECISION_AGENT_ID,
            stage="repeat_decision",
            query_id=query_id,
            case_id=case_id,
            payload=payload,
        )
        if decision is None:
            return {
                "status": "FAILED",
                "decision": None,
                "confidence": None,
                "decision_reason": execution.get("message")
                or "M8.4 Runtime recommendation failed.",
                "evidence_chain": [],
                "key_differences": [],
                "validation_required": [],
                "risks": [],
                "recommended_actions": [],
            }, execution
        return {"status": "SUCCESS", **decision}, execution

    @staticmethod
    def _analysis_mapping(
        query_id: str,
        candidates: list[dict[str, Any]],
        overall_status: str,
        warnings: list[dict[str, Any]],
    ) -> dict[str, Any]:
        report_candidates: list[dict[str, Any]] = []
        for candidate in candidates:
            recommendation = candidate.get("ai_recommendation") or {}
            similarity = candidate.get("agent_similarity") or {}
            solution = candidate.get("agent_solution") or {}
            sim_analysis = similarity.get("analysis") or {}
            confidence = (
                recommendation.get("confidence")
                if recommendation.get("status") == "SUCCESS"
                else sim_analysis.get("confidence")
            )
            report_candidates.append({
                "case_id": candidate.get("case_id"),
                "retrieval_rank": candidate.get("rank") or 0,
                "final_rank": candidate.get("rank") or 0,
                "decision": recommendation.get("decision")
                or "INSUFFICIENT_EVIDENCE",
                "confidence": float(confidence or 0.0),
                "decision_reason": recommendation.get("decision_reason") or "",
                "evidence_chain": deepcopy(
                    recommendation.get("evidence_chain") or []
                ),
                "key_differences": deepcopy(
                    recommendation.get("key_differences")
                    or sim_analysis.get("key_differences")
                    or []
                ),
                "validation_required": deepcopy(
                    recommendation.get("validation_required") or []
                ),
                "risks": deepcopy(recommendation.get("risks") or []),
                "recommended_actions": deepcopy(
                    recommendation.get("recommended_actions") or []
                ),
                "similarity": deepcopy(similarity),
                "solution": deepcopy(solution),
                "comparison_context": deepcopy(
                    candidate.get("agent_comparison_context") or {}
                ),
            })

        best = report_candidates[0] if report_candidates else {}
        return {
            "metadata": {
                "query_id": query_id,
                "decision_version": "M8.4-OPTIONAL-AI-RECOMMENDATION",
            },
            "final_decision": best.get("decision") or "INSUFFICIENT_EVIDENCE",
            "overall_confidence": float(best.get("confidence") or 0.0),
            "best_case": {
                key: best.get(key)
                for key in (
                    "case_id",
                    "final_rank",
                    "decision",
                    "confidence",
                    "decision_reason",
                )
            } if best else {},
            "candidates": report_candidates,
            "analysis_status": overall_status,
            "warnings": deepcopy(warnings),
        }

    def _deliver_report(
        self,
        query_id: str,
        candidates: list[dict[str, Any]],
        overall_status: str,
        warnings: list[dict[str, Any]],
    ) -> dict[str, Any]:
        analysis = self._analysis_mapping(
            query_id,
            candidates,
            overall_status,
            warnings,
        )
        delivery = DeliveryService(self.report_root).deliver(
            analysis,
            source_artifact=f"repeat-result/v1:{query_id}",
        )

        def relative(path_value: Any) -> str:
            path = Path(str(path_value or "")).resolve()
            try:
                return path.relative_to(self.project_root).as_posix()
            except ValueError:
                return path.name

        return {
            "status": "AVAILABLE",
            "contract": "REPEAT_CASE_REPORT/1.0",
            "analysis_status": delivery.get("analysis_status"),
            "ai_recommendation": delivery.get("decision"),
            "confidence": delivery.get("confidence"),
            "report_json_ref": relative(delivery.get("report_json")),
            "report_markdown_ref": relative(delivery.get("report_markdown")),
        }

    def analyze(
        self,
        query_trace: dict[str, Any],
        search_result: dict[str, Any],
    ) -> dict[str, Any]:
        enriched = deepcopy(search_result)
        query_id = _text(search_result.get("query_id") or query_trace.get("query_id"))
        query_input = deepcopy(search_result.get("query_input") or {})
        raw_candidates = enriched.get("candidates")
        if not isinstance(raw_candidates, list) or not raw_candidates:
            enriched["agent_analysis"] = {
                "status": "NOT_APPLICABLE",
                "m82_similarity": "NOT_RUN",
                "m83_solution": "NOT_RUN",
                "m84_recommendation": (
                    "ENABLED" if self.decision_enabled else "DISABLED"
                ),
                "provider_boundary": "UNIFIED_RUNTIME_ONLY",
            }
            enriched["analysis_report"] = {
                "status": "NOT_GENERATED",
                "reason": "NO_CANDIDATES",
            }
            return enriched

        analyzed: list[dict[str, Any]] = []
        contexts: dict[int, dict[str, Any]] = {}
        warnings: list[dict[str, Any]] = []
        runtime_error: Exception | None = None

        # M8.2 historically evaluates every complete retrieved candidate.
        for index, candidate in enumerate(raw_candidates):
            current = deepcopy(candidate)
            context = self._candidate_context(
                query_id,
                query_input,
                current,
            )
            contexts[index] = context
            current["agent_comparison_context"] = deepcopy(context)

            if current.get("detail_status") != "SUCCESS":
                current["agent_analysis_status"] = "SKIPPED_INCOMPLETE_CANDIDATE"
                current["agent_similarity"] = {
                    "analysis_status": "SKIPPED",
                    "analysis": {},
                    "warnings": [{
                        "code": "CANDIDATE_DETAIL_INCOMPLETE",
                        "message": "M8.2 skipped because candidate detail/evidence is incomplete.",
                    }],
                }
                current["agent_solution"] = {
                    "analysis_status": "SKIPPED",
                    "analysis": {},
                    "warnings": [{
                        "code": "CANDIDATE_DETAIL_INCOMPLETE",
                        "message": "M8.3 skipped because candidate detail/evidence is incomplete.",
                    }],
                }
                current["ai_recommendation"] = {
                    "status": "SKIPPED",
                    "decision": None,
                    "decision_reason": "Candidate detail/evidence is incomplete.",
                }
                analyzed.append(current)
                continue

            try:
                similarity, _ = self._similarity(context)
                current["agent_similarity"] = similarity
            except Exception as exc:
                runtime_error = exc
                current["agent_analysis_status"] = "UNAVAILABLE"
                current["agent_similarity"] = {
                    "analysis_status": "UNAVAILABLE",
                    "analysis": {},
                    "warnings": [{
                        "code": "REPEAT_AGENT_RUNTIME_UNAVAILABLE",
                        "message": str(exc),
                    }],
                }
                current["agent_solution"] = {
                    "analysis_status": "UNAVAILABLE",
                    "analysis": {},
                    "warnings": [{
                        "code": "REPEAT_AGENT_RUNTIME_UNAVAILABLE",
                        "message": str(exc),
                    }],
                }
                current["ai_recommendation"] = {
                    "status": "UNAVAILABLE",
                    "decision": None,
                    "decision_reason": str(exc),
                }
            analyzed.append(current)

        # M8.3 mature policy: rank only by M8.2 similarity for invocation
        # selection. This does NOT mutate Retriever rank/score or product order.
        top_n, min_score = self._solution_policy()
        eligible: list[tuple[int, float]] = []
        for index, candidate in enumerate(analyzed):
            similarity = candidate.get("agent_similarity") or {}
            analysis = similarity.get("analysis") or {}
            if (
                candidate.get("detail_status") == "SUCCESS"
                and similarity.get("analysis_status") == "SUCCESS"
            ):
                try:
                    score = float(analysis.get("overall_score") or 0.0)
                except (TypeError, ValueError):
                    score = 0.0
                if score >= min_score:
                    eligible.append((index, score))
        eligible.sort(key=lambda item: item[1], reverse=True)
        selected_indexes = {
            index for index, _ in (eligible[:top_n] if top_n > 0 else eligible)
        }

        for index, current in enumerate(analyzed):
            if current.get("detail_status") != "SUCCESS":
                continue
            if current.get("agent_analysis_status") == "UNAVAILABLE":
                continue

            context = contexts[index]
            similarity = current.get("agent_similarity") or {}
            if index in selected_indexes:
                try:
                    solution, _ = self._solution(context, similarity)
                except Exception as exc:
                    runtime_error = exc
                    solution = {
                        "analysis_status": "UNAVAILABLE",
                        "analysis": {},
                        "warnings": [{
                            "code": "REPEAT_AGENT_RUNTIME_UNAVAILABLE",
                            "message": str(exc),
                        }],
                    }
                current["agent_solution"] = solution
            else:
                current["agent_solution"] = {
                    "analysis_status": "SKIPPED",
                    "analysis": {},
                    "warnings": [{
                        "code": "M83_NOT_SELECTED_BY_MATURE_POLICY",
                        "message": (
                            "Historical M8.3 candidate_top_n/min_similarity_score "
                            "policy skipped this candidate."
                        ),
                    }],
                }

            try:
                recommendation, _ = self._recommendation(
                    context,
                    similarity,
                    current["agent_solution"],
                )
            except Exception as exc:
                runtime_error = exc
                recommendation = {
                    "status": "UNAVAILABLE",
                    "decision": None,
                    "decision_reason": str(exc),
                }
            current["ai_recommendation"] = recommendation

            similarity_ok = similarity.get("analysis_status") == "SUCCESS"
            solution_status = (current.get("agent_solution") or {}).get(
                "analysis_status"
            )
            solution_ok = solution_status in {"SUCCESS", "SKIPPED"}
            current["agent_analysis_status"] = (
                "SUCCESS" if similarity_ok and solution_ok else "PARTIAL"
            )

        complete_candidates = [
            item for item in analyzed if item.get("detail_status") == "SUCCESS"
        ]
        successful_candidates = [
            item
            for item in complete_candidates
            if item.get("agent_analysis_status") == "SUCCESS"
        ]
        if complete_candidates and len(successful_candidates) == len(complete_candidates):
            overall_status = "SUCCESS"
        elif successful_candidates:
            overall_status = "PARTIAL_SUCCESS"
        elif runtime_error is not None:
            overall_status = "UNAVAILABLE"
        else:
            overall_status = "SKIPPED"

        if runtime_error is not None:
            warnings.append({
                "code": "REPEAT_AGENT_RUNTIME_UNAVAILABLE",
                "message": str(runtime_error),
            })

        enriched["candidates"] = analyzed
        enriched["agent_analysis"] = {
            "status": overall_status,
            "m82_similarity": "RESTORED",
            "m83_solution": "RESTORED",
            "m83_candidate_top_n": top_n,
            "m83_min_similarity_score": min_score,
            "m84_recommendation": (
                "ENABLED" if self.decision_enabled else "DISABLED"
            ),
            "provider_boundary": "UNIFIED_RUNTIME_ONLY",
            "warnings": deepcopy(warnings),
        }

        try:
            enriched["analysis_report"] = self._deliver_report(
                query_id,
                analyzed,
                overall_status,
                warnings,
            )
        except Exception as exc:
            enriched["analysis_report"] = {
                "status": "FAILED",
                "reason": str(exc),
            }
            enriched["agent_analysis"]["warnings"].append({
                "code": "REPEAT_REPORT_DELIVERY_FAILED",
                "message": str(exc),
            })
        return enriched


__all__ = [
    "RepeatAgentAnalysisService",
    "RepeatSimilarityDTO",
    "RepeatSimilarityDimensionsDTO",
    "RepeatSimilarityDimensionDTO",
    "RepeatSolutionDTO",
    "RepeatDecisionDTO",
    "RepeatEvidenceDTO",
]
