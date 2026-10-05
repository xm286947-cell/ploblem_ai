from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from storage_life.knowledge_release import KnowledgeReleaseConsumer, KnowledgeReleaseError
from storage_life.lifetime_engine import (
    LifetimeAssessmentRequest,
    LifetimeAssessmentStatus,
    LifetimeEngine,
)
from storage_life.software_impact import (
    SoftwareImpactAnalysisRequest,
    SoftwareImpactAnalysisStatus,
    SoftwareImpactEngine,
)


SKILLS_ROOT = Path(__file__).resolve().parent
DECISION_BOUNDARY = "NO_AUTO_REPLACEMENT_DECISION"

PACK_FILES = {
    "PACK_WRITE_GOVERNANCE": "write_governance.yaml",
    "PACK_LIFETIME_ENGINEERING": "lifetime_engineering.yaml",
    "PACK_DIAGNOSTIC_VALIDATION": "diagnostic_validation.yaml",
    "PACK_CHANGE_IMPACT": "change_impact.yaml",
}

OBJECT_TYPE_ALIASES = {
    "FACT": "KnowledgeFact",
    "KNOWLEDGEFACT": "KnowledgeFact",
    "CONCEPT": "TechnicalConcept",
    "TECHNICALCONCEPT": "TechnicalConcept",
    "SOLUTION": "TechnicalSolution",
    "TECHNICALSOLUTION": "TechnicalSolution",
    "DIAGNOSTIC": "DiagnosticMethod",
    "DIAGNOSTICMETHOD": "DiagnosticMethod",
    "REQUIREMENT": "SoftwareRequirementKnowledge",
    "SOFTWAREREQUIREMENTKNOWLEDGE": "SoftwareRequirementKnowledge",
}


class SkillExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class PackPolicy:
    pack_id: str
    allowed_object_types: frozenset[str]
    current_release_version: str
    material_refs: tuple[str, ...]


def _load_pack(pack_id: str) -> PackPolicy:
    try:
        path = SKILLS_ROOT / "packs" / PACK_FILES[pack_id]
    except KeyError as exc:
        raise SkillExecutionError(f"UNKNOWN_SKILL_PACK:{pack_id}") from exc
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SkillExecutionError(f"SKILL_PACK_INVALID:{pack_id}") from exc
    dependency = payload.get("knowledge_release_dependency") or {}
    return PackPolicy(
        pack_id=str(payload.get("pack_id") or pack_id),
        allowed_object_types=frozenset(str(x) for x in payload.get("allowed_object_types") or []),
        current_release_version=str(dependency.get("current_release_version") or ""),
        material_refs=tuple(str(x) for x in payload.get("material_refs") or []),
    )


def _canonical_object_type(value: Any) -> str:
    raw = str(value or "").strip()
    return OBJECT_TYPE_ALIASES.get(raw.upper(), raw)


def _text(item: dict[str, Any]) -> str:
    return str(item.get("summary") or item.get("content") or item.get("title") or "").strip()


class StorageDomainSkillAdapter:
    """Thin executable boundary for Storage Domain Skills V0.1.

    This adapter deliberately owns no provider stack, no knowledge store, and no
    lifetime formula. It composes existing released knowledge and deterministic
    product engines only.
    """

    def __init__(
        self,
        *,
        knowledge_consumer: KnowledgeReleaseConsumer | Any | None = None,
        lifetime_engine: LifetimeEngine | None = None,
        software_impact_engine: SoftwareImpactEngine | None = None,
    ) -> None:
        self.knowledge_consumer = knowledge_consumer or KnowledgeReleaseConsumer.current()
        self.lifetime_engine = lifetime_engine or LifetimeEngine()
        self.software_impact_engine = software_impact_engine or SoftwareImpactEngine()

    def query_pack(
        self,
        pack_id: str,
        question: str,
        *,
        device_type: str = "",
        top_k: int = 12,
    ) -> dict[str, Any]:
        pack = _load_pack(pack_id)
        status = self.knowledge_consumer.status()
        if not status.get("available") or status.get("status") != "READY":
            return {
                "status": "INSUFFICIENT_KNOWLEDGE",
                "pack_id": pack_id,
                "knowledge_refs": [],
                "evidence_refs": [],
                "items": [],
                "missing_information": [str(status.get("code") or "FORMAL_KNOWLEDGE_RELEASE_REQUIRED")],
            }
        release_version = str(status.get("knowledge_release_version") or "")
        if pack.current_release_version and release_version != pack.current_release_version:
            return {
                "status": "INSUFFICIENT_KNOWLEDGE",
                "pack_id": pack_id,
                "knowledge_refs": [],
                "evidence_refs": [],
                "items": [],
                "missing_information": [
                    f"RELEASE_VERSION_MISMATCH:expected={pack.current_release_version},actual={release_version}"
                ],
            }
        try:
            result = self.knowledge_consumer.query(
                question,
                device_type=device_type,
                top_k=top_k,
                knowledge_release_version=release_version,
            )
        except KnowledgeReleaseError as exc:
            return {
                "status": "INSUFFICIENT_KNOWLEDGE",
                "pack_id": pack_id,
                "knowledge_refs": [],
                "evidence_refs": [],
                "items": [],
                "missing_information": [str(exc)],
            }

        items: list[dict[str, Any]] = []
        for item in result.get("results") or []:
            if str(item.get("status") or "ACTIVE") != "ACTIVE":
                continue
            evidence_refs = [str(x) for x in item.get("evidence_refs") or [] if str(x)]
            if not evidence_refs:
                continue
            canonical = _canonical_object_type(item.get("object_type"))
            if pack.allowed_object_types and canonical not in pack.allowed_object_types:
                continue
            items.append({**item, "canonical_object_type": canonical})

        if not items:
            return {
                "status": "INSUFFICIENT_KNOWLEDGE",
                "pack_id": pack_id,
                "knowledge_release_version": release_version,
                "knowledge_refs": [],
                "evidence_refs": [],
                "items": [],
                "missing_information": ["NO_MATCHING_RELEASED_KNOWLEDGE_WITH_EVIDENCE"],
            }

        return {
            "status": "READY",
            "pack_id": pack_id,
            "knowledge_release_version": release_version,
            "knowledge_refs": sorted({str(x.get("object_id")) for x in items if x.get("object_id")}),
            "evidence_refs": sorted({str(ref) for x in items for ref in x.get("evidence_refs") or [] if ref}),
            "items": items,
            "missing_information": [],
        }

    @staticmethod
    def _base_result(skill_id: str, status: str, direct_answer: str, structured_result: dict[str, Any],
                     *, knowledge_refs=(), evidence_refs=(), missing=(), separation=None) -> dict[str, Any]:
        return {
            "skill_id": skill_id,
            "status": status,
            "direct_answer": direct_answer,
            "structured_result": structured_result,
            "fact_derived_hypothesis_separation": separation or {
                "facts": [],
                "derived": [],
                "hypotheses": [],
                "unknowns": list(missing),
            },
            "knowledge_refs": sorted(set(knowledge_refs)),
            "evidence_refs": sorted(set(evidence_refs)),
            "missing_information": sorted(set(missing)),
            "decision_boundary": DECISION_BOUNDARY,
        }

    def execute_write_governance(self, *, device_type: str, user_context: dict[str, Any],
                                 workload_software_facts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        behaviors = list(workload_software_facts or [])
        question = str(user_context.get("question") or "software write amplification persistence wear controls")
        knowledge = self.query_pack("PACK_WRITE_GOVERNANCE", question, device_type=device_type)
        items = knowledge.get("items") or []
        mechanisms = [_text(x) for x in items if _text(x)]
        controls = [
            _text(x) for x in items
            if x.get("canonical_object_type") in {"TechnicalSolution", "SoftwareRequirementKnowledge"} and _text(x)
        ]
        missing = list(knowledge.get("missing_information") or [])
        status = "ANSWERED" if items else "INSUFFICIENT_KNOWLEDGE"
        answer = (
            "已基于正式知识与证据给出写入机制、工程控制与验证关注点。"
            if items else
            "当前正式知识发布中没有足够的可证据化写治理知识，保持 Fail-Closed。"
        )
        return self._base_result(
            "storage-write-governance", status, answer,
            {
                "observed_or_declared_behavior": behaviors,
                "potential_mechanism": mechanisms,
                "potential_risk": mechanisms,
                "engineering_control_options": controls,
                "conditions_and_limits": [str(x.get("limitations") or []) for x in items if x.get("limitations")],
                "missing_information": missing,
                "suggested_validation": controls,
                "evidence_refs": knowledge.get("evidence_refs") or [],
                "confidence_basis": ["FORMAL_KNOWLEDGE_RELEASE", "EVIDENCE_TRACEABLE"] if items else [],
                "review_roles": ["Storage Engineering", "Software", "Test"],
            },
            knowledge_refs=knowledge.get("knowledge_refs") or [],
            evidence_refs=knowledge.get("evidence_refs") or [],
            missing=missing,
            separation={"facts": mechanisms, "derived": [], "hypotheses": [], "unknowns": missing},
        )

    def execute_lifetime_budget(self, *, request: LifetimeAssessmentRequest, requested_metric: str,
                                target_service_life: dict[str, Any]) -> dict[str, Any]:
        result = self.lifetime_engine.assess(request, requested_metric)
        missing = list(result.missing_inputs) + list(result.error_details)
        target = dict(target_service_life or {})
        # The UI and newer product adapters use the frozen formal metric id,
        # while older callers may still use the compatibility alias.
        target_supported_metrics = {"ssd.dwpd", "SSD_DWPD_OBSERVED_V1"}
        target_supported = not bool(target) or requested_metric in target_supported_metrics
        if target and requested_metric not in target_supported_metrics:
            missing.append("TARGET_SERVICE_LIFE_BUDGET_FORMULA_NOT_REGISTERED")
        status_map = {
            LifetimeAssessmentStatus.CALCULATED: "ANSWERED" if target_supported else "PARTIAL",
            LifetimeAssessmentStatus.INSUFFICIENT_DATA: "INSUFFICIENT_DATA",
            LifetimeAssessmentStatus.INVALID_INPUT: "INVALID_INPUT",
            LifetimeAssessmentStatus.NOT_APPLICABLE: "UNSUPPORTED",
        }
        status = status_map[result.status]
        write_budget = {}
        if "rated_tbw_bytes" in result.inputs:
            write_budget["rated_tbw_bytes"] = result.inputs["rated_tbw_bytes"]
        answer = (
            "已通过现有确定性 Lifetime Engine 计算；目标服役期若无已注册公式则不自行外推。"
            if result.status == LifetimeAssessmentStatus.CALCULATED
            else "现有 Lifetime Engine 无法在当前输入下形成确定性结果，保持 Fail-Closed。"
        )
        return self._base_result(
            "storage-lifetime-budget", status, answer,
            {
                "applicable_formula": {"formula_id": result.formula_id, "formula_version": result.formula_version},
                "endurance_basis": [result.inputs],
                "write_budget": write_budget,
                "measured_vs_budget": result.result if isinstance(result.result, dict) else {"value": result.result, "unit": result.unit},
                "margin_status": result.status.value,
                "assumptions": [x.model_dump(mode="json") for x in result.assumptions],
                "missing_information": sorted(set(missing)),
                "evidence_refs": list(result.evidence_refs),
                "formula_replay_refs": [result.replay_trace],
            },
            knowledge_refs=result.knowledge_refs,
            evidence_refs=result.evidence_refs,
            missing=missing,
            separation={
                "facts": [result.inputs],
                "derived": [result.result] if result.result is not None else [],
                "hypotheses": [],
                "unknowns": sorted(set(missing)),
            },
        )

    def execute_diagnostic_validation(self, *, device_type: str, target_question: str,
                                      diagnostic_capabilities: list[dict[str, Any]] | None = None,
                                      runtime_observations: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        knowledge = self.query_pack("PACK_DIAGNOSTIC_VALIDATION", target_question, device_type=device_type)
        items = knowledge.get("items") or []
        observations = list(runtime_observations or [])
        current = [x for x in observations if x.get("is_formally_consumable") is True]
        stale = [x for x in observations if x.get("is_formally_consumable") is not True]
        missing = list(knowledge.get("missing_information") or [])
        if stale:
            missing.append("STALE_OR_NONCONSUMABLE_RUNTIME_OBSERVATION_EXCLUDED")
        methods = [_text(x) for x in items if x.get("canonical_object_type") == "DiagnosticMethod" and _text(x)]
        status = "ANSWERED" if items else "INSUFFICIENT_KNOWLEDGE"
        return self._base_result(
            "storage-diagnostic-validation", status,
            "已区分诊断能力与当前观测；只使用可正式消费的 Runtime Observation。"
            if items else "缺少可发布、可追溯的诊断知识，无法解释字段语义。",
            {
                "supported_metrics": list(diagnostic_capabilities or []),
                "acquisition_method": methods,
                "data_source": [x.get("source_refs") or [] for x in items],
                "current_observation": current,
                "interpretation_boundary": [_text(x) for x in items if _text(x)],
                "abnormality_signal": [],
                "validation_method": methods,
                "missing_information": sorted(set(missing)),
                "evidence_refs": knowledge.get("evidence_refs") or [],
            },
            knowledge_refs=knowledge.get("knowledge_refs") or [],
            evidence_refs=knowledge.get("evidence_refs") or [],
            missing=missing,
            separation={"facts": current, "derived": [], "hypotheses": [], "unknowns": missing},
        )

    def execute_change_impact(self, *, device_type: str, parameter_delta: list[dict[str, Any]],
                              question: str = "device parameter change lifetime software monitoring validation impact",
                              software_impact_request: SoftwareImpactAnalysisRequest | None = None) -> dict[str, Any]:
        knowledge = self.query_pack("PACK_CHANGE_IMPACT", question, device_type=device_type)
        items = knowledge.get("items") or []
        impacts = []
        impact_refs: list[str] = []
        impact_evidence: list[str] = []
        missing = list(knowledge.get("missing_information") or [])
        if software_impact_request is not None:
            analyzed = self.software_impact_engine.analyze(software_impact_request)
            impact_refs.extend(analyzed.knowledge_refs)
            impact_evidence.extend(analyzed.evidence_refs)
            missing.extend(analyzed.missing_information)
            impacts = [x.model_dump(mode="json") for x in analyzed.impacts]
            if analyzed.status not in {SoftwareImpactAnalysisStatus.EVIDENCED, SoftwareImpactAnalysisStatus.NOT_APPLICABLE}:
                missing.append(f"SOFTWARE_IMPACT:{analyzed.status.value}")
        classifications = ["KNOWLEDGE_BACKED" for _ in items]
        if parameter_delta and not items:
            classifications = ["UNKNOWN" for _ in parameter_delta]
        status = "ANSWERED" if items or impacts else "INSUFFICIENT_KNOWLEDGE"
        return self._base_result(
            "storage-change-impact", status,
            "参数差异已按正式知识/既有影响分析边界投影；未知项保持 UNKNOWN，需工程评审。"
            if status == "ANSWERED" else "当前没有足够正式知识支撑该变更影响，未知项不得视为安全。",
            {
                "parameter_changes": list(parameter_delta),
                "technical_meaning": [_text(x) for x in items if _text(x)],
                "lifetime_impact": [_text(x) for x in items if _text(x)],
                "software_impact": impacts,
                "diagnostic_monitoring_impact": [_text(x) for x in items if _text(x)],
                "validation_requirements": impacts,
                "evidence_refs": sorted(set((knowledge.get("evidence_refs") or []) + impact_evidence)),
                "unknowns": sorted(set(missing)),
                "review_roles": ["Hardware", "Software", "Test", "Storage Engineering"],
                "impact_classification": classifications,
            },
            knowledge_refs=(knowledge.get("knowledge_refs") or []) + impact_refs,
            evidence_refs=(knowledge.get("evidence_refs") or []) + impact_evidence,
            missing=missing,
            separation={
                "facts": list(parameter_delta),
                "derived": impacts,
                "hypotheses": [],
                "unknowns": sorted(set(missing)),
            },
        )
