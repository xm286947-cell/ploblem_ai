"""Isolated adapter from REQ-022 event views to the existing M8 engine."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable
from datetime import datetime, timezone
import hashlib
import json
import shutil
import yaml

from builder.m82_similarity_runner import run_m82_similarity
from builder.m83_solution_runner import run_m83_solution
from builder.m84_repeat_runner import run_m84_decision
from builder.m85_delivery_runner import run_m85_delivery
from builder.validators import validate_json
from parser.common import write_json
from .repository import MajorKnowledgeRepository


ASSETS = (
    "config/model.yaml",
    "config/repeat_decision.yaml",
    "prompts/similarity_analyzer.md",
    "prompts/solution_analyzer.md",
    "prompts/repeat_decision.md",
    "schema/similarity_analysis.schema.json",
    "schema/solution_analysis.schema.json",
    "schema/repeat_analysis.schema.json",
    "tests/samples/mock_similarity_response.json",
    "tests/samples/mock_solution_response.json",
    "tests/samples/mock_repeat_decision_response.json",
)


def _entry_text(entries: Iterable[dict], types: set[str]) -> str:
    return "\n".join(item["content"] for item in entries if item["entry_type"] in types and item["status"] in {"CONFIRMED", "CORRECTED"})


def _evidence_value(value: str) -> list[dict]:
    if not value:
        return []
    return [{
        "value": value, "source_type": "FUSED", "source_location": "REQ022_CONFIRMED_ENTRY",
        "confidence": 1.0, "evidence_refs": [],
    }]


def _cause_detail(value: str = "") -> dict:
    return {"original": "", "report": value, "standard": value, "confidence": 1.0 if value else 0.0, "evidence_refs": []}


class LegacyRepeatAdapter:
    def __init__(self, repository: MajorKnowledgeRepository, legacy_project_root: str | Path, run_root: str | Path):
        self.repository = repository
        self.legacy_project_root = Path(legacy_project_root).resolve()
        self.run_root = Path(run_root).resolve()
        self.run_root.mkdir(parents=True, exist_ok=True)

    def _prepare_root(self, run_id: str) -> Path:
        root = self.run_root / run_id
        root.mkdir(parents=True, exist_ok=True)
        for relative in ASSETS:
            source = self.legacy_project_root / relative
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        # Force serial execution inside an isolated run; product model settings are
        # otherwise preserved and no real credential is introduced.
        model_path = root / "config/model.yaml"
        model = yaml.safe_load(model_path.read_text(encoding="utf-8")) or {}
        model["parallel_ai"] = {"enabled": False, "max_workers": 1}
        model_path.write_text(yaml.safe_dump(model, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return root

    def export_event_view(self, event_id: str) -> dict:
        event = self.repository.event(event_id)
        if not event:
            raise KeyError(event_id)
        case = self.repository.get_case(event["case_id"])
        entries = self.repository.entries(event["case_id"])
        fact = _entry_text(entries, {"ISSUE_FACT"})
        cause = _entry_text(entries, {"ROOT_CAUSE"})
        action = _entry_text(entries, {"ACTION"})
        verification = _entry_text(entries, {"VERIFICATION"})
        now = datetime.now(timezone.utc).isoformat()
        standard_case = {
            "metadata": {
                "case_id": f"{event['case_id']}::{event_id}",
                "itr_id": event["standard_itr"],
                "assessment_year": "", "assessment_month": "", "report_filename": "",
                "source_excel": "", "source_report": "REQ022_CONFIRMED_ENTRIES",
                "builder_version": "REQ022-LEGACY-1", "schema_version": "1.0",
                "fusion_rule_version": "REQ022-1", "prompt_version": "REQ022-1",
                "model_version": "HUMAN_CONFIRMED", "source_file_version": str(max((item["revision_no"] for item in entries), default=0)),
                "created_at": now, "updated_at": now, "generated_at": now,
                "parse_status": "SUCCESS", "evidence_status": "HUMAN_CONFIRMED",
            },
            "business_context": {
                "ipmt": "", "spdt": "", "responsible_department_level2": "",
                "organization_path": [], "product": "", "domain": case["domain"],
            },
            "problem": {
                "original_description": fact, "report_description": fact, "standard_description": fact,
                "problem_summary": fact, "phenomenon": _evidence_value(fact), "failure_object": [],
                "trigger_condition": [], "impact": [], "event_replay": [],
            },
            "analysis": {
                "trc": {"occurrence": _cause_detail(cause), "escape": _cause_detail()},
                "mrc": {"occurrence": _cause_detail(cause), "escape": _cause_detail()},
                "five_why": [], "root_cause": _evidence_value(cause),
                "failure_mechanism": _evidence_value(cause), "contributing_factors": [],
            },
            "classification": {
                "original": {"cause_level1": "", "cause_level2": ""},
                "report_verified": {"cause_level1": "", "cause_level2": "", "evidence_refs": []},
                "ai_inferred": {"cause_level1": "", "cause_level2": "", "reason": "", "confidence": 0.0},
                "classification_conflict": False, "conflict_description": "",
            },
            "solution": {
                "original_solution": _evidence_value(action), "corrective_actions": _evidence_value(action),
                "preventive_actions": [], "management_actions": [], "technical_actions": [],
                "reusable_actions": _evidence_value(action), "action_status": [],
            },
            "knowledge": {
                "case_summary": fact, "normalized_problem": fact, "phenomenon_tags": [],
                "failure_object_tags": [], "trigger_tags": [], "failure_mechanism_tags": [],
                "cause_tags": [], "solution_tags": [], "keywords": [],
                "retrieval_text": "\n".join(value for value in (fact, cause, action, verification) if value),
                "quality_flags": [] if fact and cause else ["MISSING_ROOT_CAUSE"],
                "ai_model": "", "prompt_version": "REQ022-1", "generated_at": now,
            },
        }
        errors = validate_json(standard_case, self.legacy_project_root / "schema/standard_case.schema.json")
        if errors:
            raise ValueError("LEGACY_STANDARD_CASE_INVALID:" + ";".join(errors))
        return standard_case

    def _candidate_events(self, current: dict, limit: int = 10) -> list[dict]:
        # Same case is excluded because its case-level review conclusions cannot be
        # safely allocated to sibling events. This is stricter than merely excluding
        # one attachment/version and prevents false self-repeat findings.
        with self.repository.connect() as connection:
            rows = connection.execute(
                """SELECT e.* FROM kb_event e JOIN kb_case c ON c.case_id=e.case_id
                   WHERE e.group_code=? AND e.event_id<>? AND e.case_id<>? AND c.status='ACTIVE'
                   ORDER BY c.updated_at DESC,e.created_at DESC LIMIT ?""",
                (current["group_code"], current["event_id"], current["case_id"], limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def run(self, event_id: str, *, mock: bool = True, skip_ai: bool = False, limit: int = 10) -> dict:
        current = self.repository.event(event_id)
        if not current:
            raise KeyError(event_id)
        view = self.export_event_view(event_id)
        revision = int(view["metadata"]["source_file_version"] or 0)
        candidates = self._candidate_events(current, limit)
        input_value = {
            "event": event_id,
            "revision": revision,
            "candidates": [item["event_id"] for item in candidates],
            "adapter": "REQ022-LEGACY-1",
            "execution": "mock" if mock else ("skip-ai" if skip_ai else "configured-model"),
        }
        input_hash = hashlib.sha256(json.dumps(input_value, sort_keys=True).encode()).hexdigest()
        run, reused = self.repository.create_or_reuse_run(
            current["case_id"], None, "REPEAT_ANALYSIS", input_hash,
            model_profile="legacy-m8-mock" if mock else ("legacy-m8-skip-ai" if skip_ai else "legacy-m8-configured"),
        )
        if reused and run["state"] == "COMPLETED":
            return {"run_id": run["run_id"], "state": "COMPLETED", "reused": True}
        self.repository.set_run(run["run_id"], "RUNNING", coverage_total=len(candidates))
        root = self._prepare_root(run["run_id"])
        write_json(root / f"event_views/{event_id}.json", view)
        write_json(root / f"event_views/{event_id}.mapping.json", {
            "adapter_version": "REQ022-LEGACY-1", "source_case_id": current["case_id"],
            "source_event_id": event_id, "group_code": current["group_code"], "revision": revision,
        })
        if not candidates:
            result = self.repository.add_repeat_result(
                run["run_id"], event_id, "INSUFFICIENT_EVIDENCE", 0.0,
                legacy_result={"scope": "CURRENT_AUTHORIZED_CANDIDATES", "candidate_count": 0},
                report={"message": "当前授权候选范围未发现可比较历史事件；不能据此认定为全新问题。"},
                markdown="# 重复问题辅助分析\n\n当前授权候选范围无可比较历史事件，结论为证据不足。\n",
            )
            self.repository.set_run(run["run_id"], "COMPLETED", coverage_processed=0, error_code="NO_CANDIDATE_IN_SCOPE")
            return {"run_id": run["run_id"], "state": "COMPLETED", "reused": False, "decision": result["decision"], "candidate_count": 0}
        query_id = event_id.replace(":", "-")
        for rank, candidate in enumerate(candidates, 1):
            candidate_view = self.export_event_view(candidate["event_id"])
            case_id = candidate["event_id"]
            write_json(root / f"event_views/{case_id}.json", candidate_view)
            write_json(root / f"event_views/{case_id}.mapping.json", {
                "adapter_version": "REQ022-LEGACY-1", "source_case_id": candidate["case_id"],
                "source_event_id": candidate["event_id"], "group_code": candidate["group_code"],
                "revision": int(candidate_view["metadata"]["source_file_version"] or 0),
            })
            context = {
                "context_version": "M8.1-REQ022",
                "query_id": query_id,
                "case_id": case_id,
                "query": {"standard_query": view, "retrieval_profile": {"group_code": current["group_code"]}},
                "candidate": {"rank": rank, "score": max(0.01, 1 - rank * 0.05), "case_id": case_id},
                "case": {
                    "standard_case": candidate_view,
                    "enriched_case": candidate_view,
                    "retrieval_document": {"text": json.dumps(candidate_view, ensure_ascii=False)},
                    "raw_evidence": {}, "embedding_metadata": {},
                },
                "evidence": {"available_sources": ["REQ022_CONFIRMED_ENTRIES"], "retrieval_text": "", "report_filename": "", "matched_report_path": "", "sections": [], "unclassified_blocks": []},
                "source_paths": {"candidate_file": f"event_views/{case_id}.json"},
                "quality": {"status": "COMPLETE", "missing_sources": [], "quality_flags": []},
                "generated_at": "",
            }
            write_json(root / f"knowledge/analysis_context/{query_id}/{case_id}.json", context)
        try:
            summaries = {
                "similarity": run_m82_similarity(root, query_id=query_id, overwrite=True, mock=mock, skip_ai=skip_ai),
                "solution": run_m83_solution(root, query_id=query_id, overwrite=True, mock=mock, skip_ai=skip_ai),
                "decision": run_m84_decision(root, query_id=query_id, overwrite=True, mock=mock, skip_ai=skip_ai),
                "delivery": run_m85_delivery(root, query_id=query_id, overwrite=True),
            }
            failed = [name for name, summary in summaries.items() if summary.get("failed")]
            if failed:
                raise RuntimeError("LEGACY_STAGE_FAILED:" + ",".join(failed))
            legacy = json.loads((root / f"knowledge/repeat_analysis/{query_id}/repeat_analysis.json").read_text(encoding="utf-8"))
            report = json.loads((root / f"output/reports/{query_id}/report.json").read_text(encoding="utf-8"))
            markdown = (root / f"output/reports/{query_id}/report.md").read_text(encoding="utf-8")
            best = legacy.get("best_case") or {}
            candidate_event_id = best.get("case_id")
            candidate = next((item for item in candidates if item["event_id"] == candidate_event_id), None)
            decision = legacy.get("final_decision") or "INSUFFICIENT_EVIDENCE"
            confidence = float(legacy.get("confidence") or (legacy.get("repeat_decision") or {}).get("confidence") or 0)
            stored = self.repository.add_repeat_result(
                run["run_id"], event_id, decision, confidence,
                candidate_event_id=candidate_event_id if candidate else None,
                candidate_case_id=candidate["case_id"] if candidate else None,
                legacy_result={"analysis": legacy, "stage_summaries": summaries, "scope": "CURRENT_AUTHORIZED_CANDIDATES"},
                report=report, markdown=markdown,
            )
            self.repository.save_step(run["run_id"], "LEGACY_M8", hashlib.sha256((input_hash + ":m8").encode()).hexdigest(), "SUCCESS", input_value=input_value, result=summaries)
            self.repository.set_run(run["run_id"], "COMPLETED", coverage_processed=len(candidates))
            return {"run_id": run["run_id"], "state": "COMPLETED", "reused": False, "decision": decision, "repeat_result_id": stored["repeat_result_id"], "candidate_count": len(candidates), "run_directory": str(root)}
        except Exception as exc:
            self.repository.save_step(run["run_id"], "LEGACY_M8", hashlib.sha256((input_hash + ":m8").encode()).hexdigest(), "FAILED", input_value=input_value, error_detail=str(exc))
            self.repository.set_run(run["run_id"], "FAILED", error_code=type(exc).__name__, error_detail=str(exc))
            return {"run_id": run["run_id"], "state": "FAILED", "reused": False, "error": str(exc), "run_directory": str(root)}
