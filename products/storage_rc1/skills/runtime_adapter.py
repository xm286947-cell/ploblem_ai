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
from runtime.contracts import RuntimeObservation


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
        assessment_context = dict(user_context.get("assessment_context") or {})
        query_parts = [
            str(user_context.get("question") or "software write amplification persistence wear controls")
        ]
        for item in behaviors:
            description = str(item.get("description") or "").strip()
            if description:
                query_parts.append(description[:1200])
        for key in ("latest_lifetime", "latest_diagnosis"):
            item = assessment_context.get(key)
            if isinstance(item, dict):
                query_parts.append(
                    " ".join(
                        str(x)
                        for x in (
                            key,
                            item.get("status"),
                            item.get("direct_answer"),
                            item.get("next_action"),
                        )
                        if str(x or "").strip()
                    )[:1600]
                )
                risk_context = item.get("risk_context")
                if isinstance(risk_context, dict) and risk_context:
                    query_parts.append(f"{key} risk context {risk_context}"[:2200])
        metrics = assessment_context.get("runtime_metrics")
        if isinstance(metrics, list) and metrics:
            query_parts.append("runtime metrics " + " ".join(str(x) for x in metrics[:20]))
        runtime_context = assessment_context.get("runtime_context")
        if isinstance(runtime_context, list) and runtime_context:
            query_parts.append(f"runtime trend context {runtime_context[:12]}"[:2400])
        question = " ".join(x for x in query_parts if x.strip())
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
        # Target service-life projection is only defined for observed SSD DWPD.
        # It reuses the existing DWPD result plus the confirmed rated TBW Device Fact;
        # no second lifetime formula/engine is introduced.
        target_supported_metrics = {"ssd.dwpd", "SSD_DWPD_OBSERVED_V1"}
        target_projection: dict[str, Any] = {}
        target_supported = not bool(target)

        if target:
            if requested_metric not in target_supported_metrics:
                missing.append("TARGET_SERVICE_LIFE_BUDGET_FORMULA_NOT_REGISTERED")
            elif result.status == LifetimeAssessmentStatus.CALCULATED:
                try:
                    target_value = float(target.get("value"))
                    target_unit = str(target.get("unit") or "").strip().lower()
                    if target_unit not in {"year", "years", "yr", "yrs"}:
                        raise ValueError("TARGET_SERVICE_LIFE_UNIT_UNSUPPORTED")
                    if target_value <= 0:
                        raise ValueError("TARGET_SERVICE_LIFE_OUT_OF_RANGE")
                    observed_dwpd = float(result.result)
                    capacity_bytes = float(result.inputs["capacity_bytes"])
                    target_days = target_value * 365.25
                    projected_host_bytes = observed_dwpd * capacity_bytes * target_days

                    rated_fact = next(
                        (fact for fact in request.confirmed_facts if fact.metric_name == "rated_tbw_bytes"),
                        None,
                    )
                    if rated_fact is None:
                        missing.append("rated_tbw_bytes:CONFIRMED_DEVICE_FACT_REQUIRED_FOR_TARGET_LIFE")
                        target_projection = {
                            "target_service_life": {"value": target_value, "unit": "years"},
                            "target_days": target_days,
                            "observed_dwpd": observed_dwpd,
                            "projected_host_written_bytes": projected_host_bytes,
                            "budget_status": "BUDGET_UNAVAILABLE",
                        }
                    else:
                        try:
                            rated_tbw_bytes, _ = self.lifetime_engine._normalize(
                                rated_fact.value,
                                rated_fact.unit,
                                "bytes",
                                "rated_tbw_bytes",
                            )
                        except Exception as exc:
                            details = getattr(exc, "details", None)
                            if details:
                                missing.extend(str(x) for x in details)
                            else:
                                missing.append("RATED_TBW_NORMALIZATION_FAILED")
                            rated_tbw_bytes = None
                        if rated_tbw_bytes is None:
                            target_projection = {
                                "target_service_life": {"value": target_value, "unit": "years"},
                                "target_days": target_days,
                                "observed_dwpd": observed_dwpd,
                                "projected_host_written_bytes": projected_host_bytes,
                                "budget_status": "BUDGET_UNAVAILABLE",
                            }
                        elif rated_tbw_bytes <= 0:
                            raise ValueError("RATED_TBW_OUT_OF_RANGE")
                        else:
                            consumed_ratio_at_target = projected_host_bytes / rated_tbw_bytes
                            remaining_bytes_at_target = rated_tbw_bytes - projected_host_bytes
                            target_projection = {
                            "target_service_life": {"value": target_value, "unit": "years"},
                            "target_days": target_days,
                            "observed_dwpd": observed_dwpd,
                            "capacity_bytes": capacity_bytes,
                            "projected_host_written_bytes": projected_host_bytes,
                            "rated_tbw_bytes": rated_tbw_bytes,
                            "consumed_ratio_at_target": consumed_ratio_at_target,
                            "remaining_bytes_at_target": remaining_bytes_at_target,
                            "budget_status": (
                                "WITHIN_BUDGET"
                                if projected_host_bytes <= rated_tbw_bytes
                                else "EXCEEDS_BUDGET"
                            ),
                                "evidence_refs": sorted(
                                    set(list(result.evidence_refs) + list(rated_fact.evidence_refs))
                                ),
                            }
                            target_supported = True
                except (TypeError, ValueError, KeyError) as exc:
                    missing.append(str(exc))
            else:
                missing.append("TARGET_SERVICE_LIFE_REQUIRES_CALCULATED_DWPD")

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
        if target_projection.get("rated_tbw_bytes") is not None:
            write_budget["rated_tbw_bytes"] = target_projection["rated_tbw_bytes"]

        if result.status == LifetimeAssessmentStatus.CALCULATED:
            if target and target_projection.get("budget_status") == "WITHIN_BUDGET":
                answer = "已按当前观测 DWPD 投影到目标服役期，预计累计写入未超过已确认 TBW 预算。"
            elif target and target_projection.get("budget_status") == "EXCEEDS_BUDGET":
                answer = "已按当前观测 DWPD 投影到目标服役期，预计累计写入将超过已确认 TBW 预算。"
            elif target:
                answer = "已计算当前 DWPD，但目标服役期预算仍缺少已确认 TBW 或有效目标寿命输入。"
            else:
                answer = "已通过现有确定性 Lifetime Engine 计算；未提供目标服役期时不自行外推剩余寿命年限。"
        else:
            answer = "现有 Lifetime Engine 无法在当前输入下形成确定性结果，保持 Fail-Closed。"

        return self._base_result(
            "storage-lifetime-budget", status, answer,
            {
                "applicable_formula": {"formula_id": result.formula_id, "formula_version": result.formula_version},
                "endurance_basis": [result.inputs],
                "write_budget": write_budget,
                "measured_vs_budget": result.result if isinstance(result.result, dict) else {"value": result.result, "unit": result.unit},
                "target_service_life_projection": target_projection,
                "margin_status": (
                    target_projection.get("budget_status")
                    or result.status.value
                ),
                "assumptions": [x.model_dump(mode="json") for x in result.assumptions],
                "missing_information": sorted(set(missing)),
                "evidence_refs": sorted(
                    set(
                        list(result.evidence_refs)
                        + list(target_projection.get("evidence_refs") or [])
                    )
                ),
                "formula_replay_refs": [result.replay_trace],
            },
            knowledge_refs=result.knowledge_refs,
            evidence_refs=sorted(
                set(
                    list(result.evidence_refs)
                    + list(target_projection.get("evidence_refs") or [])
                )
            ),
            missing=missing,
            separation={
                "facts": [result.inputs],
                "derived": [
                    x for x in (result.result, target_projection or None)
                    if x is not None
                ],
                "hypotheses": [],
                "unknowns": sorted(set(missing)),
            },
        )

    @staticmethod
    def _runtime_number(value: Any) -> float | None:
        if isinstance(value, bool) or value is None:
            return None
        raw = str(value).strip().replace(",", "").rstrip("%")
        try:
            if raw.lower().startswith("0x"):
                return float(int(raw, 16))
            return float(raw)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _deterministic_abnormality_signals(
        cls,
        observations: list[dict[str, Any]],
        *,
        released_semantics: set[str],
        diagnostic_capabilities: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Evaluate only explicit, fail-closed runtime signals.

        No vendor threshold is invented here.  Signals either use explicit
        counter/flag semantics or a threshold supplied in the same observation
        set.  Protocol-tier interpretations that require released semantics are
        gated by the diagnostic knowledge release.
        """
        by_name = {str(x.get("metric_name") or ""): x for x in observations}

        def number(name: str) -> float | None:
            item = by_name.get(name) or {}
            return cls._runtime_number(item.get("normalized_value", item.get("raw_value")))

        def confirmed_fact_threshold(*names: str) -> tuple[float | None, dict[str, Any] | None]:
            wanted = {str(x) for x in names}
            for capability in diagnostic_capabilities or []:
                if str(capability.get("canonical_name") or "") not in wanted:
                    continue
                if str(capability.get("review_status") or "").upper() != "CONFIRMED":
                    continue
                refs = [str(x) for x in (capability.get("evidence_refs") or []) if str(x)]
                if not refs:
                    continue
                value = cls._runtime_number(capability.get("datasheet_fact"))
                if value is not None:
                    return value, capability
            return None, None

        def signal(name: str, code: str, severity: str, rationale: str) -> dict[str, Any]:
            item = by_name.get(name) or {}
            return {
                "metric_name": name,
                "code": code,
                "severity": severity,
                "observed_value": item.get("normalized_value", item.get("raw_value")),
                "unit": item.get("unit"),
                "evidence_ref": item.get("evidence_ref") or item.get("raw_output_ref"),
                "rationale": rationale,
            }

        signals: list[dict[str, Any]] = []
        critical_warning = number("critical_warning")
        if critical_warning is not None and critical_warning != 0:
            signals.append(signal(
                "critical_warning",
                "NVME_CRITICAL_WARNING_NONZERO",
                "CRITICAL",
                "Critical Warning 为非零显式告警位；需进入人工诊断。",
            ))

        for metric, code, severity in (
            ("media_errors", "MEDIA_ERROR_PRESENT", "WARNING"),
            ("ecc_uncorrectable", "UNCORRECTABLE_ECC_PRESENT", "CRITICAL"),
            ("runtime_bad_block", "RUNTIME_BAD_BLOCK_PRESENT", "WARNING"),
            ("program_fail", "PROGRAM_FAIL_PRESENT", "WARNING"),
            ("erase_fail", "ERASE_FAIL_PRESENT", "WARNING"),
        ):
            value = number(metric)
            if value is not None and value > 0:
                signals.append(signal(
                    metric,
                    code,
                    severity,
                    f"{metric} 显式计数大于 0；需要结合时间趋势、负载和原始日志继续判定。",
                ))

        spare = number("available_spare")
        spare_threshold = number("available_spare_threshold")
        spare_threshold_source = "RUNTIME_OBSERVATION"
        spare_threshold_refs: list[str] = []
        if spare_threshold is not None:
            threshold_obs = by_name.get("available_spare_threshold") or {}
            ref = threshold_obs.get("evidence_ref") or threshold_obs.get("raw_output_ref")
            if ref:
                spare_threshold_refs.append(str(ref))
        else:
            spare_threshold, spare_capability = confirmed_fact_threshold("spare_threshold")
            if spare_threshold is not None and spare_capability is not None:
                spare_threshold_source = "CONFIRMED_DEVICE_FACT"
                spare_threshold_refs = [
                    str(x) for x in (spare_capability.get("evidence_refs") or []) if str(x)
                ]
        if spare is not None and spare_threshold is not None and spare < spare_threshold:
            item = signal(
                "available_spare",
                "AVAILABLE_SPARE_BELOW_THRESHOLD",
                "CRITICAL",
                "Available Spare 低于显式阈值；阈值来自同次运行观测或已确认 Device Fact。",
            )
            item.update({
                "threshold_value": spare_threshold,
                "threshold_source": spare_threshold_source,
                "threshold_evidence_refs": spare_threshold_refs,
            })
            signals.append(item)

        bit_flips = number("bit_flip_count")
        bit_flip_threshold = number("bit_flip_threshold")
        bit_flip_threshold_source = "RUNTIME_OBSERVATION"
        bit_flip_threshold_refs: list[str] = []
        if bit_flip_threshold is not None:
            threshold_obs = by_name.get("bit_flip_threshold") or {}
            ref = threshold_obs.get("evidence_ref") or threshold_obs.get("raw_output_ref")
            if ref:
                bit_flip_threshold_refs.append(str(ref))
        else:
            bit_flip_threshold, bit_flip_capability = confirmed_fact_threshold("bit_flip_threshold")
            if bit_flip_threshold is not None and bit_flip_capability is not None:
                bit_flip_threshold_source = "CONFIRMED_DEVICE_FACT"
                bit_flip_threshold_refs = [
                    str(x) for x in (bit_flip_capability.get("evidence_refs") or []) if str(x)
                ]
        if (
            bit_flips is not None
            and bit_flip_threshold is not None
            and bit_flips >= bit_flip_threshold
        ):
            item = signal(
                "bit_flip_count",
                "BIT_FLIP_AT_OR_ABOVE_EXPLICIT_THRESHOLD",
                "WARNING",
                "Bit Flip 数量达到或超过显式阈值；需结合 ECC 裕量和趋势继续诊断。",
            )
            item.update({
                "threshold_value": bit_flip_threshold,
                "threshold_source": bit_flip_threshold_source,
                "threshold_evidence_refs": bit_flip_threshold_refs,
            })
            signals.append(item)

        if "pre_eol_info" in released_semantics:
            pre_eol = number("pre_eol_info")
            if pre_eol == 3:
                signals.append(signal(
                    "pre_eol_info",
                    "EMMC_PRE_EOL_URGENT",
                    "CRITICAL",
                    "PRE_EOL_INFO 精确匹配已发布语义中的紧急状态。",
                ))
            elif pre_eol == 2:
                signals.append(signal(
                    "pre_eol_info",
                    "EMMC_PRE_EOL_WARNING",
                    "WARNING",
                    "PRE_EOL_INFO 精确匹配已发布语义中的预警状态。",
                ))

        if "percentage_used" in released_semantics:
            percentage_used = number("percentage_used")
            if percentage_used is not None and percentage_used >= 100:
                signals.append(signal(
                    "percentage_used",
                    "NVME_PERCENTAGE_USED_AT_OR_ABOVE_100",
                    "WARNING",
                    "Percentage Used 已达到或超过已发布语义中的额定耐久消耗边界；仍需结合厂商规格与工作负载判断。",
                ))

        return signals

    def execute_diagnostic_validation(self, *, device_type: str, target_question: str,
                                      diagnostic_capabilities: list[dict[str, Any]] | None = None,
                                      runtime_observations: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        observations = list(runtime_observations or [])
        capabilities = list(diagnostic_capabilities or [])
        metric_terms = [
            str(x.get("metric_name") or "").strip()
            for x in observations
            if isinstance(x, dict) and str(x.get("metric_name") or "").strip()
        ]
        capability_terms = [
            str(x.get("canonical_name") or "").strip()
            for x in capabilities
            if isinstance(x, dict) and str(x.get("canonical_name") or "").strip()
        ]
        knowledge_query = " ".join(
            x for x in [
                str(target_question or "").strip(),
                " ".join(metric_terms[:20]),
                " ".join(capability_terms[:20]),
            ] if x
        )
        knowledge = self.query_pack(
            "PACK_DIAGNOSTIC_VALIDATION",
            knowledge_query,
            device_type=device_type,
        )
        items = knowledge.get("items") or []
        current: list[dict[str, Any]] = []
        excluded: list[str] = []

        for raw in observations:
            try:
                observation = RuntimeObservation(**dict(raw))
            except Exception:
                excluded.append("INVALID_RUNTIME_OBSERVATION_CONTRACT")
                continue
            if observation.is_formally_consumable:
                current.append(observation.model_dump(mode="json"))
            else:
                excluded.append("STALE_OR_NONCONSUMABLE_RUNTIME_OBSERVATION_EXCLUDED")

        missing = list(knowledge.get("missing_information") or []) + excluded
        methods = [_text(x) for x in items if x.get("canonical_object_type") == "DiagnosticMethod" and _text(x)]
        semantic_text = " ".join(_text(x).lower() for x in items if _text(x))
        released_semantics: set[str] = set()
        if "pre_eol" in semantic_text or "pre eol" in semantic_text:
            released_semantics.add("pre_eol_info")
        if "percentage used" in semantic_text or "percentage_used" in semantic_text:
            released_semantics.add("percentage_used")
        signals = self._deterministic_abnormality_signals(
            current,
            released_semantics=released_semantics,
            diagnostic_capabilities=capabilities,
        )

        if not current:
            status = "INSUFFICIENT_DATA"
            missing.append("FORMALLY_CONSUMABLE_RUNTIME_OBSERVATION_REQUIRED")
            answer = "当前没有可正式消费的 Runtime Observation，不能形成运行诊断结果。"
        elif signals:
            status = "ANSWERED"
            if not items:
                missing.append("FORMAL_DIAGNOSTIC_KNOWLEDGE_REQUIRED_FOR_FULL_INTERPRETATION")
            answer = (
                f"检测到 {len(signals)} 个确定性异常/退化信号；详细机理与处置边界仍需结合 Evidence、趋势和正式知识。"
            )
        elif not items:
            status = "INSUFFICIENT_KNOWLEDGE"
            answer = "已有当前运行观测，但没有触发无需协议语义即可判定的显式异常信号，且缺少正式诊断知识，保持 Fail-Closed。"
        else:
            status = "ANSWERED"
            answer = "当前可正式消费观测未触发已注册的确定性异常信号；这不等同于证明介质无风险。"

        diagnosis_status = (
            "ABNORMAL_SIGNAL_PRESENT"
            if signals else
            "NO_REGISTERED_SIGNAL"
            if current and items else
            "INCOMPLETE"
        )
        signal_evidence_refs = sorted({
            str(ref)
            for item in signals
            for ref in (
                [item.get("evidence_ref")]
                + list(item.get("threshold_evidence_refs") or [])
            )
            if str(ref or "").strip()
        })
        combined_evidence_refs = sorted({
            str(ref)
            for ref in list(knowledge.get("evidence_refs") or []) + signal_evidence_refs
            if str(ref or "").strip()
        })
        return self._base_result(
            "storage-diagnostic-validation", status,
            answer,
            {
                "supported_metrics": list(diagnostic_capabilities or []),
                "acquisition_method": methods,
                "data_source": [x.get("source_refs") or [] for x in items],
                "current_observation": current,
                "diagnosis_status": diagnosis_status,
                "interpretation_boundary": [_text(x) for x in items if _text(x)],
                "abnormality_signal": signals,
                "validation_method": methods,
                "missing_information": sorted(set(missing)),
                "evidence_refs": combined_evidence_refs,
            },
            knowledge_refs=knowledge.get("knowledge_refs") or [],
            evidence_refs=combined_evidence_refs,
            missing=missing,
            separation={
                "facts": current,
                "derived": signals,
                "hypotheses": [],
                "unknowns": sorted(set(missing)),
            },
        )

    def execute_change_impact(self, *, device_type: str, parameter_delta: list[dict[str, Any]],
                              question: str = "device parameter change lifetime software monitoring validation impact",
                              software_impact_request: SoftwareImpactAnalysisRequest | None = None) -> dict[str, Any]:
        delta = list(parameter_delta or [])
        delta_terms = [
            str(x.get("canonical_name") or "").strip()
            for x in delta
            if isinstance(x, dict) and str(x.get("canonical_name") or "").strip()
        ]
        knowledge_query = " ".join(
            x for x in [
                str(question or "").strip(),
                " ".join(delta_terms[:30]),
            ] if x
        )
        knowledge = self.query_pack("PACK_CHANGE_IMPACT", knowledge_query, device_type=device_type)
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
        if delta and not items:
            classifications = ["UNKNOWN" for _ in delta]
        status = "ANSWERED" if items or impacts else "INSUFFICIENT_KNOWLEDGE"
        return self._base_result(
            "storage-change-impact", status,
            "参数差异已按正式知识/既有影响分析边界投影；未知项保持 UNKNOWN，需工程评审。"
            if status == "ANSWERED" else "当前没有足够正式知识支撑该变更影响，未知项不得视为安全。",
            {
                "parameter_changes": delta,
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
                "facts": delta,
                "derived": impacts,
                "hypotheses": [],
                "unknowns": sorted(set(missing)),
            },
        )
