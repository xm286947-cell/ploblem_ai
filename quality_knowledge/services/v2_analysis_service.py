"""Native V2 four-stage analysis orchestration for the clean P0 database."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from quality_knowledge.models.analysis_v2 import (
    CapabilityGapV2DTO,
    EscapeAnalysisV2DTO,
    EvidenceV2DTO,
    EvidenceValueV2DTO,
    IssueAnalysisV2Envelope,
    MrcV2DTO,
    OccurrenceAnalysisV2DTO,
    OpenQuestionV2DTO,
    RecurrenceAnalysisV2DTO,
)
from quality_knowledge.response_normalizer_v2 import normalize_stage_v2
from quality_knowledge.taxonomy.seed import TAXONOMY_VERSION_ID


STAGES = ("occurrence", "escape", "recurrence", "capability_gap")


class V2AnalysisError(RuntimeError):
    """A stable domain error for V2 analysis requests."""


@dataclass(frozen=True)
class StageRunContext:
    """Frozen runner input, shared by all stages of one Analysis Set."""

    analysis_set_id: str
    stage: str
    knowledge_id: str
    issue_version_id: str
    input_hash: str
    normalized_snapshot: dict[str, Any]
    raw_json: dict[str, Any]
    taxonomy_version_id: str
    classification_mapping_versions: dict[str, str]
    prompt_versions: dict[str, str]
    scoring_version_id: str | None
    analysis_profile: dict[str, Any]
    human_confirmation_context: list[dict[str, Any]]
    completed_stages: dict[str, Any]


class V2StageRunner(Protocol):
    """Injectable adapter boundary for an actual model/prompt implementation."""

    def run_stage(self, *, stage: str, context: StageRunContext) -> dict[str, Any]:
        """Return only the native V2 JSON object for ``stage``."""


class V2AnalysisService:
    def __init__(
        self,
        repository: Any,
        stage_runner: V2StageRunner | Any | None = None,
        progress_callback: Any | None = None,
    ):
        self.repository = repository
        self.stage_runner = stage_runner
        self.progress_callback = progress_callback

    def run(
        self,
        knowledge_id: str,
        request: dict[str, Any] | None = None,
    ) -> IssueAnalysisV2Envelope:
        """Run all V2 stages or return an exact-input immutable prior result."""

        request = dict(request or {})
        issue = self.repository.get_issue(knowledge_id)
        if issue is None or not issue.get("issue_version_id"):
            raise V2AnalysisError("ISSUE_NOT_FOUND")
        if self.stage_runner is None:
            raise V2AnalysisError("ANALYSIS_RUNNER_NOT_CONFIGURED")

        frozen = self._freeze_request(issue, request)
        if not request.get("force"):
            existing = self.repository.get_analysis_set_by_input_hash(
                issue["issue_version_id"], frozen["input_hash"]
            )
            if existing is not None:
                return self._envelope_from_record(existing)

        analysis_set_id = f"ASV2-{uuid.uuid4().hex}"
        parsed_stages: dict[str, Any] = {}
        stage_rows: list[dict[str, Any]] = []
        warnings: list[str] = []

        for stage in STAGES:
            self._notify_progress(stage, "RUNNING")
            context = StageRunContext(
                analysis_set_id=analysis_set_id,
                stage=stage,
                knowledge_id=knowledge_id,
                issue_version_id=issue["issue_version_id"],
                input_hash=frozen["input_hash"],
                normalized_snapshot=issue["normalized_snapshot"],
                raw_json=issue["raw_json"],
                taxonomy_version_id=frozen["taxonomy_version_id"],
                classification_mapping_versions=frozen["classification_mapping_versions"],
                prompt_versions=frozen["prompt_versions"],
                scoring_version_id=frozen["scoring_version_id"],
                analysis_profile=frozen["analysis_profile"],
                human_confirmation_context=frozen["human_confirmation_context"],
                completed_stages=dict(parsed_stages),
            )
            try:
                execution = self._run_stage(stage, context)
                raw_result = execution["payload"]
                parsed = normalize_stage_v2(stage, raw_result)
                parsed_stages[stage] = parsed
                self._notify_progress(stage, "COMPLETED")
                stage_rows.append(
                    {
                        "stage": stage,
                        "status": "COMPLETED",
                        "input_hash": frozen["input_hash"],
                        "model_name": execution.get("model_name") or request.get("model_name"),
                        "prompt_version_id": self._prompt_fk(request, stage),
                        "raw_response": execution.get("raw_response") or self._dump(raw_result),
                        "parsed_result": self._model_dump(parsed),
                        "debug": execution.get("debug", {}),
                    }
                )
            except Exception as error:  # A later stage may still produce useful evidence.
                message = str(error)
                self._notify_progress(stage, "FAILED", error=message)
                warnings.append(f"{stage}:{message}")
                debug = getattr(error, "debug", {"retry_errors": [message]})
                stage_rows.append(
                    {
                        "stage": stage,
                        "status": "FAILED",
                        "input_hash": frozen["input_hash"],
                        "model_name": request.get("model_name"),
                        "prompt_version_id": self._prompt_fk(request, stage),
                        "raw_response": None,
                        "parsed_result": None,
                        "validation_error": message,
                        "debug": debug,
                    }
                )

        completed_count = len(parsed_stages)
        status = "COMPLETED" if completed_count == len(STAGES) else (
            "FAILED" if completed_count == 0 else "PARTIAL_FAILED"
        )
        analysis = {
            "analysis_set_id": analysis_set_id,
            "knowledge_id": knowledge_id,
            "issue_version_id": issue["issue_version_id"],
            "taxonomy_version_id": frozen["taxonomy_version_id"],
            "classification_mapping_versions": frozen["classification_mapping_versions"],
            "prompt_versions": frozen["prompt_versions"],
            "scoring_version_id": frozen["scoring_version_id"],
            "analysis_profile": frozen["analysis_profile"],
            "source_coverage": frozen["source_coverage"],
            "input_hash": frozen["input_hash"],
            "status": status,
            "classification_consistency": "PENDING_CONFIRMATION",
            "stages": stage_rows,
            **self._projections(parsed_stages),
        }
        self.repository.save_analysis_set(analysis)
        return self._make_envelope(analysis, parsed_stages, warnings)

    def _notify_progress(self, stage: str, status: str, **details: Any) -> None:
        if self.progress_callback is None:
            return
        try:
            self.progress_callback({"stage": stage, "status": status, **details})
        except Exception:
            # UI progress must never change the analysis outcome.
            return

    def get(self, knowledge_id: str) -> IssueAnalysisV2Envelope:
        """Read only a native P0 V2 Analysis Set for an issue."""

        record = self.repository.get_latest_analysis_set(knowledge_id)
        if record is None:
            raise V2AnalysisError("V2_ANALYSIS_NOT_AVAILABLE")
        return self._envelope_from_record(record)

    def _run_stage(self, stage: str, context: StageRunContext) -> dict[str, Any]:
        runner = self.stage_runner
        if hasattr(runner, "run_stage"):
            result = runner.run_stage(stage=stage, context=context)
        elif callable(runner):
            result = runner(stage=stage, context=context)
        else:
            raise V2AnalysisError("ANALYSIS_RUNNER_INVALID")
        if hasattr(result, "payload"):
            return {
                "payload": result.payload,
                "raw_response": getattr(result, "raw_response", None),
                "model_name": getattr(result, "model_name", None),
                "debug": getattr(result, "debug", {}),
            }
        return {"payload": result, "raw_response": None, "model_name": None, "debug": {}}

    def _freeze_request(self, issue: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        active_prompts = {stage: self.repository.get_active_prompt(stage) for stage in STAGES}
        prompt_versions = request.get("prompt_versions") or {
            stage: active_prompts[stage]["prompt_version_id"] for stage in STAGES
        }
        if set(prompt_versions) != set(STAGES) or not all(prompt_versions.values()):
            raise V2AnalysisError("PROMPT_VERSIONS_REQUIRED_FOR_ALL_STAGES")
        classification_versions = request.get("classification_mapping_versions") or {
            "mapping_config_id": str(issue["mapping_config_id"]),
            "mapping_config_version": str(issue["mapping_config_version"]),
            "standard_catalog_version_id": str(issue["standard_catalog_version_id"]),
        }
        taxonomy_version_id = str(request.get("taxonomy_version_id") or TAXONOMY_VERSION_ID)
        profile = dict(request.get("analysis_profile") or {})
        source_coverage = {
            "raw_source_available": bool(issue["raw_json"]),
            "normalized_snapshot_available": bool(issue["normalized_snapshot"]),
        }
        scoring_version_id = request.get("scoring_version_id") or self.repository.get_active_scoring_version()["scoring_version_id"]
        human_context = self._human_confirmation_context(issue["knowledge_id"])
        frozen = {
            "knowledge_id": issue["knowledge_id"],
            "issue_version_id": issue["issue_version_id"],
            "normalized_hash": issue["normalized_hash"],
            "taxonomy_version_id": taxonomy_version_id,
            "classification_mapping_versions": classification_versions,
            "prompt_versions": prompt_versions,
            "scoring_version_id": scoring_version_id,
            "analysis_profile": profile,
            "source_coverage": source_coverage,
            "human_confirmation_context": human_context,
        }
        if request.get("force"):
            frozen["force_nonce"] = str(request.get("force_nonce") or uuid.uuid4().hex)
        frozen["input_hash"] = self._hash(frozen)
        return frozen

    def _prompt_fk(self, request: dict[str, Any], stage: str) -> str | None:
        prompt_ids = request.get("prompt_version_ids") or {}
        return prompt_ids.get(stage) or self.repository.get_active_prompt(stage)["prompt_version_id"]

    def _human_confirmation_context(self, knowledge_id: str) -> list[dict[str, Any]]:
        previous = self.repository.get_latest_analysis_set(knowledge_id)
        if previous is None:
            return []
        revisions = self.repository.get_human_revisions(previous["analysis_set_id"])
        if not revisions:
            return []
        return [
            {
                "target_path": answer["target_path"],
                "confirmed_value": answer["confirmed_value_json"],
                "evidence": answer["evidence_json"],
            }
            for answer in revisions[0]["answers"]
        ]

    def _make_envelope(
        self,
        analysis: dict[str, Any],
        stages: dict[str, Any],
        warnings: list[str],
    ) -> IssueAnalysisV2Envelope:
        return IssueAnalysisV2Envelope(
            contract_version="2.0.0",
            analysis_set_id=analysis["analysis_set_id"],
            knowledge_id=analysis["knowledge_id"],
            issue_version_id=analysis["issue_version_id"],
            taxonomy_version_id=analysis["taxonomy_version_id"],
            classification_mapping_versions=analysis["classification_mapping_versions"],
            prompt_versions=analysis["prompt_versions"],
            scoring_version_id=analysis["scoring_version_id"],
            analysis_profile=analysis["analysis_profile"],
            source_coverage=analysis["source_coverage"],
            input_hash=analysis["input_hash"],
            status=analysis["status"],
            occurrence=stages.get("occurrence"),
            escape=stages.get("escape"),
            recurrence=stages.get("recurrence"),
            capability_gaps=stages.get("capability_gap", []),
            classification_consistency=analysis["classification_consistency"],
            warnings=warnings,
        )

    def _envelope_from_record(self, record: dict[str, Any]) -> IssueAnalysisV2Envelope:
        record = {
            **record,
            "classification_mapping_versions": record.get(
                "classification_mapping_versions", record.get("classification_mapping_versions_json", {})
            ),
            "prompt_versions": record.get("prompt_versions", record.get("prompt_versions_json", {})),
            "analysis_profile": record.get("analysis_profile", record.get("analysis_profile_json", {})),
            "source_coverage": record.get("source_coverage", record.get("source_coverage_json", {})),
            "classification_consistency": record.get("classification_consistency") or "PENDING_CONFIRMATION",
        }
        parsed: dict[str, Any] = {}
        warnings: list[str] = []
        for stage_run in record["stage_runs"]:
            stage = stage_run["stage"]
            if stage_run["status"] == "COMPLETED" and stage_run["parsed_result_json"] is not None:
                persisted = stage_run["parsed_result_json"]
                if stage == "capability_gap":
                    persisted = {"capability_gaps": persisted}
                parsed[stage] = normalize_stage_v2(stage, persisted)
            elif stage_run["status"] == "FAILED":
                warnings.append(f"{stage}:{stage_run.get('validation_error') or 'FAILED'}")
        return self._make_envelope(record, parsed, warnings)

    def _projections(self, stages: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        values: list[dict[str, Any]] = []
        tags: list[dict[str, Any]] = []
        mrc: list[dict[str, Any]] = []
        gaps: list[dict[str, Any]] = []
        questions: list[dict[str, Any]] = []
        evidence: list[dict[str, Any]] = []

        occurrence = stages.get("occurrence")
        if occurrence is not None:
            self._add_value(values, evidence, "occurrence", "engineering_root_cause", occurrence.engineering_root_cause)
            for index, item in enumerate(occurrence.management_contributing_factors):
                self._add_value(values, evidence, "occurrence", f"management_contributing_factors.{index}", item)
            self._add_tags(tags, evidence, "occurrence", occurrence)
            self._add_mrc(mrc, evidence, occurrence.mrc)
            self._add_questions(questions, "occurrence", occurrence.open_questions)
            self._add_evidence(evidence, "occurrence", "", occurrence.evidence)

        escape = stages.get("escape")
        if escape is not None:
            self._add_value(values, evidence, "escape", "escape_mechanism", escape.escape_mechanism)
            self._add_value(values, evidence, "escape", "missing_or_failed_control", escape.missing_or_failed_control)
            self._add_value(values, evidence, "escape", "known_issue_leakage", escape.known_issue_leakage)
            self._add_mrc(mrc, evidence, escape.mrc)
            self._add_questions(questions, "escape", escape.open_questions)
            self._add_evidence(evidence, "escape", "", escape.evidence)

        recurrence = stages.get("recurrence")
        if recurrence is not None:
            self._add_value(values, evidence, "recurrence", "recurrence_risk_level", recurrence.recurrence_risk_level)
            self._add_value(values, evidence, "recurrence", "existing_control_coverage", recurrence.existing_control_coverage)
            self._add_value(values, evidence, "recurrence", "residual_risk", recurrence.residual_risk)
            self._add_value(values, evidence, "recurrence", "potential_affected_products", recurrence.potential_affected_products)
            self._add_value(values, evidence, "recurrence", "potential_affected_versions", recurrence.potential_affected_versions)
            self._add_value(values, evidence, "recurrence", "horizontal_action_needed", recurrence.horizontal_action_needed)
            self._add_value(values, evidence, "recurrence", "customer_impact", recurrence.customer_impact)
            self._add_questions(questions, "recurrence", recurrence.open_questions)

        for gap in stages.get("capability_gap", []):
            gaps.append(
                {
                    "capability_axis": gap.capability_axis,
                    "capability_code": gap.capability_code,
                    "governance_scope": gap.governance_scope,
                    "control_status": gap.control_status,
                    "details": gap.model_dump(mode="json", exclude={"capability_axis", "capability_code", "governance_scope", "control_status", "source_type", "confidence", "evidence"}),
                    "source_type": gap.source_type,
                    "confidence": gap.confidence,
                }
            )
            self._add_evidence(evidence, "capability_gap", f"capability_gaps.{gap.capability_code}", gap.evidence)
        return {
            "values": values,
            "tags": tags,
            "mrc": mrc,
            "capability_gaps": gaps,
            "open_questions": questions,
            "evidence": evidence,
        }

    def _add_tags(self, tags: list[dict[str, Any]], evidence: list[dict[str, Any]], stage: str, result: OccurrenceAnalysisV2DTO) -> None:
        for collection in (
            result.domain_tags, result.lifecycle_tags, result.trigger_tags, result.failure_mechanism_tags,
        ):
            for tag in collection:
                tags.append({
                    "stage": stage, "axis": tag.axis, "tag_code": tag.code,
                    "tag_role": tag.role, "source_type": tag.source_type,
                    "confidence": tag.confidence,
                })
                self._add_evidence(evidence, stage, f"tags.{tag.axis}.{tag.code}", tag.evidence)

    def _add_mrc(self, mrc_rows: list[dict[str, Any]], evidence: list[dict[str, Any]], mrc: MrcV2DTO) -> None:
        terms = ([mrc.primary] if mrc.primary else []) + mrc.secondary
        for term in terms:
            mrc_rows.append({
                "side": mrc.side, "mrc_code": term.code, "role": term.role,
                "control_status": mrc.control_status, "source_type": term.source_type,
                "confidence": term.confidence,
            })
            self._add_evidence(evidence, mrc.side.lower(), f"mrc.{term.role.lower()}", term.evidence)

    def _add_value(self, values: list[dict[str, Any]], evidence: list[dict[str, Any]], stage: str, path: str, value: Any) -> None:
        data = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
        source_type = data.get("source_type", "AI_INFERRED") if isinstance(data, dict) else "AI_INFERRED"
        confidence = data.get("confidence", 0.0) if isinstance(data, dict) else 0.0
        values.append({"stage": stage, "value_path": f"{stage}.{path}", "value": data, "source_type": source_type, "confidence": confidence})
        if isinstance(value, (EvidenceValueV2DTO,)):
            self._add_evidence(evidence, stage, f"{stage}.{path}", value.evidence)
        elif hasattr(value, "evidence"):
            self._add_evidence(evidence, stage, f"{stage}.{path}", value.evidence)

    @staticmethod
    def _add_questions(rows: list[dict[str, Any]], stage: str, questions: list[OpenQuestionV2DTO]) -> None:
        for question in questions:
            rows.append({
                "stage": stage,
                "target_path": question.target_path,
                "question_key": question.question_key,
                "question_text": question.question,
                "priority": question.priority,
                "options": question.suggested_options,
            })

    @staticmethod
    def _add_evidence(rows: list[dict[str, Any]], stage: str, target_path: str, evidence_items: list[EvidenceV2DTO]) -> None:
        for item in evidence_items:
            rows.append({
                "stage": stage,
                "target_path": target_path,
                "source_type": item.source_type,
                "source_ref": item.source_ref,
                "field_path": item.field_path,
                "excerpt": item.excerpt,
                "confidence": item.confidence,
            })

    @staticmethod
    def _model_dump(value: Any) -> Any:
        if isinstance(value, list):
            return [item.model_dump(mode="json") for item in value]
        return value.model_dump(mode="json")

    @staticmethod
    def _hash(value: dict[str, Any]) -> str:
        return hashlib.sha256(V2AnalysisService._dump(value).encode("utf-8")).hexdigest()

    @staticmethod
    def _dump(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
