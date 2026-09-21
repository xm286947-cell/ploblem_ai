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
from builder.retrieval_document_builder import RetrievalDocumentBuilder
from retriever.case_retriever import CaseRetriever, QueryInput
from parser.common import write_json
from .repository import MajorKnowledgeRepository
from .restore import MajorCaseRestoreService


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
    values = []
    for item in entries:
        if item["entry_type"] not in types or item["status"] not in {"CONFIRMED", "CORRECTED"}:
            continue
        prefix = "[CASE_SHARED] " if item.get("scope_kind") == "CASE_SHARED" else ""
        values.append(prefix + item["content"])
    return "\n".join(values)


def _evidence_value(value: str) -> list[dict]:
    if not value:
        return []
    return [{
        "value": value, "source_type": "FUSED", "source_location": "REQ022_CONFIRMED_ENTRY",
        "confidence": 1.0, "evidence_refs": [],
    }]


def _cause_detail(value: str = "") -> dict:
    return {"original": "", "report": value, "standard": value, "confidence": 1.0 if value else 0.0, "evidence_refs": []}


class _MemoryCaseRetriever(CaseRetriever):
    """Run the existing M7 CaseRetriever against repository-backed event views."""

    def __init__(
        self,
        root: Path,
        app: dict,
        model: dict,
        config: dict,
        records: list[dict],
        documents: dict[str, dict],
        cases: dict[str, dict],
    ) -> None:
        super().__init__(root, app, model, config)
        self._records = records
        self._documents = documents
        self._cases = cases
        self._embeddings: dict[str, dict] = {}
        for case_id, document in documents.items():
            response = self.embedding_client.embed(str(document.get("text") or ""))
            self._embeddings[case_id] = {
                "vector": response.vector,
                "model": response.model,
            }

    def _load_index(self) -> list[dict]:
        return self._records

    def _load_document(self, record: dict) -> dict:
        return self._documents[record["case_id"]]

    def _load_embedding(self, record: dict) -> dict:
        return self._embeddings[record["case_id"]]

    def _load_case(self, document: dict) -> dict:
        return self._cases[document["case_id"]]


class LegacyRepeatAdapter:
    def __init__(self, repository: MajorKnowledgeRepository, legacy_project_root: str | Path, run_root: str | Path):
        self.repository = repository
        self.legacy_project_root = Path(legacy_project_root).resolve()
        self.run_root = Path(run_root).resolve()
        self.run_root.mkdir(parents=True, exist_ok=True)
        self.features = MajorCaseRestoreService(repository, self.legacy_project_root)

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
        entries = self.repository.entries_for_event(event_id)
        unscoped = self.repository.unscoped_confirmed_entries(event["case_id"])
        feature_view = self.features.feature_view(event["case_id"], event_id)
        effective = feature_view.get("effective_features") or {}

        def feature(name: str) -> str:
            value = (effective.get(name) or {}).get("value")
            if isinstance(value, list):
                return "\n".join(str(item) for item in value if str(item).strip())
            return str(value or "").strip()

        fact = feature("issue_fact") or _entry_text(entries, {"ISSUE_FACT"})
        cause = feature("root_cause") or feature("failure_mechanism") or _entry_text(entries, {"ROOT_CAUSE"})
        action = feature("solution") or _entry_text(entries, {"ACTION"})
        verification = feature("verification") or _entry_text(entries, {"VERIFICATION"})
        source_fact = feature_view.get("source_fact") or {}
        documents = feature_view.get("document_evidence") or []
        now = datetime.now(timezone.utc).isoformat()
        standard_case = {
            "metadata": {
                "case_id": f"{event['case_id']}::{event_id}",
                "itr_id": event["standard_itr"],
                "assessment_year": feature("assessment_year"), "assessment_month": feature("assessment_month"),
                "report_filename": feature("report_filename"),
                "source_excel": str(source_fact.get("source_ref") or ""),
                "source_report": ",".join(doc.get("filename", "") for doc in documents if doc.get("filename")) or "REQ022_CASE_FEATURE_VIEW",
                "builder_version": "REQ022-LEGACY-4", "schema_version": "1.0",
                "fusion_rule_version": "REQ022-2", "prompt_version": "REQ022-1",
                "model_version": "CASE_FEATURE_VIEW",
                "source_file_version": str(max(
                    [int(source_fact.get("revision_no") or 0)]
                    + [int(item["revision_no"]) for item in entries]
                )),
                "created_at": now, "updated_at": now, "generated_at": now,
                "parse_status": "SUCCESS", "evidence_status": "HUMAN_CONFIRMED",
            },
            "business_context": {
                "ipmt": feature("ipmt"), "spdt": feature("spdt"),
                "responsible_department_level2": feature("responsible_department"),
                "organization_path": [value for value in (feature("ipmt"), feature("spdt"), feature("responsible_department")) if value],
                "product": feature("product"), "domain": case["domain"],
            },
            "problem": {
                "original_description": fact, "report_description": fact, "standard_description": fact,
                "problem_summary": fact, "phenomenon": _evidence_value(fact),
                "failure_object": _evidence_value(feature("component")),
                "trigger_condition": _evidence_value(feature("trigger_condition")), "impact": [], "event_replay": [],
            },
            "analysis": {
                "trc": {
                    "occurrence": _cause_detail(feature("trc_occurrence") or cause),
                    "escape": _cause_detail(feature("trc_escape")),
                },
                "mrc": {
                    "occurrence": _cause_detail(feature("mrc_occurrence")),
                    "escape": _cause_detail(feature("mrc_escape")),
                },
                "five_why": [], "root_cause": _evidence_value(feature("root_cause") or cause),
                "failure_mechanism": _evidence_value(feature("failure_mechanism") or cause), "contributing_factors": [],
            },
            "classification": {
                "original": {"cause_level1": feature("classification_l1"), "cause_level2": feature("classification_l2")},
                "report_verified": {"cause_level1": feature("classification_l1"), "cause_level2": feature("classification_l2"), "evidence_refs": []},
                "ai_inferred": {"cause_level1": "", "cause_level2": "", "reason": "", "confidence": 0.0},
                "classification_conflict": False, "conflict_description": "",
            },
            "solution": {
                "original_solution": _evidence_value(action), "corrective_actions": _evidence_value(action),
                "preventive_actions": [], "management_actions": _evidence_value(feature("mrc_occurrence")),
                "technical_actions": _evidence_value(feature("trc_occurrence")),
                "reusable_actions": _evidence_value(action), "action_status": [],
            },
            "knowledge": {
                "case_summary": fact, "normalized_problem": fact, "phenomenon_tags": [],
                "failure_object_tags": [], "trigger_tags": [], "failure_mechanism_tags": [],
                "cause_tags": [], "solution_tags": [], "keywords": [],
                "retrieval_text": "\n".join(
                    value for value in (
                        fact, feature("product"), feature("module"), feature("component"),
                        feature("trc_occurrence"), feature("trc_escape"),
                        feature("mrc_occurrence"), feature("mrc_escape"),
                        cause, feature("failure_mechanism"), feature("trigger_condition"),
                        action, verification,
                    ) if value
                ),
                "quality_flags": (
                    ([] if fact and cause else ["MISSING_ROOT_CAUSE"])
                    + (["UNSCOPED_EVENT_KNOWLEDGE"] if unscoped else [])
                ),
                "ai_model": "", "prompt_version": "REQ022-1", "generated_at": now,
            },
        }
        errors = validate_json(standard_case, self.legacy_project_root / "schema/standard_case.schema.json")
        if errors:
            raise ValueError("LEGACY_STANDARD_CASE_INVALID:" + ";".join(errors))
        return standard_case

    def _knowledge_fingerprint(self, case_id: str, event_id: str | None = None) -> str:
        entries = self.repository.entries_for_event(event_id) if event_id else self.repository.entries(case_id)
        payload = []
        for item in entries:
            evidence = sorted(
                [
                    {
                        "fragment_id": ev.get("fragment_id"),
                        "source_link_id": ev.get("source_link_id"),
                        "locator": ev.get("locator", ""),
                        "excerpt": ev.get("excerpt", ""),
                    }
                    for ev in item.get("evidence", [])
                ],
                key=lambda ev: (
                    str(ev.get("fragment_id") or ""),
                    str(ev.get("source_link_id") or ""),
                    str(ev.get("locator") or ""),
                    str(ev.get("excerpt") or ""),
                ),
            )
            payload.append({
                "entry_id": item["entry_id"],
                "entry_type": item["entry_type"],
                "status": item["status"],
                "current_revision_id": item["current_revision_id"],
                "revision_no": item["revision_no"],
                "content": item["content"],
                "event_id": item.get("event_id"),
                "scope_kind": item.get("scope_kind", "UNSCOPED"),
                "evidence": evidence,
            })
        payload.sort(key=lambda item: (item["entry_type"], item["entry_id"]))
        feature_view = self.features.feature_view(case_id, event_id)
        feature_payload = {
            key: {
                "value": item.get("value"),
                "source_layer": item.get("source_layer"),
                "revision": item.get("revision"),
            }
            for key, item in (feature_view.get("effective_features") or {}).items()
        }
        fingerprint_payload = {
            "entries": payload,
            "source_fact_revision": (feature_view.get("source_fact") or {}).get("revision_no"),
            "effective_features": feature_payload,
        }
        return hashlib.sha256(json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

    def _retrieval_config_hash(self) -> str:
        payload = {
            "app": yaml.safe_load((self.legacy_project_root / "config/app.yaml").read_text(encoding="utf-8")) or {},
            "model_embedding": (yaml.safe_load((self.legacy_project_root / "config/model.yaml").read_text(encoding="utf-8")) or {}).get("embedding", {}),
            "retrieval": yaml.safe_load((self.legacy_project_root / "config/retrieval.yaml").read_text(encoding="utf-8")) or {},
        }
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

    @staticmethod
    def _query_from_view(view: dict) -> QueryInput:
        def values(items: Iterable[dict]) -> str:
            return "\n".join(
                str(item.get("value") or "").strip()
                for item in items or []
                if str(item.get("value") or "").strip()
            )

        problem = view.get("problem", {})
        analysis = view.get("analysis", {})
        solution = view.get("solution", {})
        knowledge = view.get("knowledge", {})
        business = view.get("business_context", {})
        text = (
            str(knowledge.get("normalized_problem") or "").strip()
            or str(problem.get("standard_description") or "").strip()
            or str(problem.get("report_description") or "").strip()
            or str(knowledge.get("retrieval_text") or "").strip()
        )
        cause = "\n".join(filter(None, [
            values(analysis.get("root_cause", [])),
            values(analysis.get("failure_mechanism", [])),
        ]))
        solution_text = "\n".join(filter(None, [
            values(solution.get("corrective_actions", [])),
            values(solution.get("preventive_actions", [])),
            values(solution.get("reusable_actions", [])),
        ]))
        return QueryInput(
            text=text,
            cause_description=cause,
            solution=solution_text,
            ipmt=str(business.get("ipmt") or ""),
            spdt=str(business.get("spdt") or ""),
            responsible_department_level2=str(business.get("responsible_department_level2") or ""),
            product=str(business.get("product") or ""),
            domain=str(business.get("domain") or ""),
        )

    def _candidate_events(self, current: dict, limit: int = 10) -> list[dict]:
        # Authorization/isolation happens before similarity retrieval. Only ACTIVE
        # cases in the same data group are eligible, and the entire current case is
        # excluded so sibling events cannot become self-repeat candidates.
        with self.repository.connect() as connection:
            rows = connection.execute(
                """SELECT e.* FROM kb_event e JOIN kb_case c ON c.case_id=e.case_id
                   WHERE e.group_code=? AND e.case_id<>? AND c.status='ACTIVE'""",
                (current["group_code"], current["case_id"]),
            ).fetchall()
        pool = [dict(row) for row in rows]
        if not pool:
            return []

        builder = RetrievalDocumentBuilder()
        documents: dict[str, dict] = {}
        cases: dict[str, dict] = {}
        records: list[dict] = []
        event_by_retrieval_case: dict[str, dict] = {}
        for candidate in pool:
            view = self.export_event_view(candidate["event_id"])
            document = builder.build(view, "")
            retrieval_case_id = document["case_id"]
            documents[retrieval_case_id] = document
            cases[retrieval_case_id] = view
            event_by_retrieval_case[retrieval_case_id] = candidate
            records.append({
                "document_id": document["document_id"],
                "case_id": retrieval_case_id,
                "title": document["title"],
                "organization": document["organization"],
                "classification": document["classification"],
                "filters": document["filters"],
                "tags": document["tags"],
                "quality_flags": document["quality_flags"],
                "content_hash": document["content_hash"],
                "embedding_path": f"memory://{retrieval_case_id}/embedding",
                "retrieval_doc_path": f"memory://{retrieval_case_id}/retrieval",
            })

        app = yaml.safe_load((self.legacy_project_root / "config/app.yaml").read_text(encoding="utf-8")) or {}
        model = yaml.safe_load((self.legacy_project_root / "config/model.yaml").read_text(encoding="utf-8")) or {}
        retrieval_config = yaml.safe_load((self.legacy_project_root / "config/retrieval.yaml").read_text(encoding="utf-8")) or {}
        retriever = _MemoryCaseRetriever(
            self.legacy_project_root, app, model, retrieval_config, records, documents, cases
        )
        current_view = self.export_event_view(current["event_id"])
        result = retriever.search(self._query_from_view(current_view), top_k=limit)

        ranked: list[dict] = []
        for item in result.get("results", []):
            candidate = dict(event_by_retrieval_case[item["case_id"]])
            candidate["retrieval_rank"] = int(item["rank"])
            candidate["retrieval_score"] = float(item["score"])
            candidate["retrieval_score_breakdown"] = item.get("score_breakdown", {})
            candidate["retrieval_reasons"] = item.get("reasons", [])
            candidate["knowledge_fingerprint"] = self._knowledge_fingerprint(candidate["case_id"], candidate["event_id"])
            ranked.append(candidate)
        return ranked

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
            "current_knowledge_fingerprint": self._knowledge_fingerprint(current["case_id"], event_id),
            "candidates": [
                {
                    "event_id": item["event_id"],
                    "case_id": item["case_id"],
                    "knowledge_fingerprint": item["knowledge_fingerprint"],
                    "retrieval_score": item["retrieval_score"],
                    "retrieval_rank": item["retrieval_rank"],
                }
                for item in candidates
            ],
            "retrieval_config_hash": self._retrieval_config_hash(),
            "adapter": "REQ022-LEGACY-3",
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
            "adapter_version": "REQ022-LEGACY-3", "source_case_id": current["case_id"],
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
                "adapter_version": "REQ022-LEGACY-3", "source_case_id": candidate["case_id"],
                "source_event_id": candidate["event_id"], "group_code": candidate["group_code"],
                "revision": int(candidate_view["metadata"]["source_file_version"] or 0),
            })
            context = {
                "context_version": "M8.1-REQ022",
                "query_id": query_id,
                "case_id": case_id,
                "query": {"standard_query": view, "retrieval_profile": {"group_code": current["group_code"]}},
                "candidate": {
                    "rank": int(candidate.get("retrieval_rank") or rank),
                    "score": float(candidate.get("retrieval_score") or 0.0),
                    "case_id": case_id,
                    "score_breakdown": candidate.get("retrieval_score_breakdown", {}),
                    "reasons": candidate.get("retrieval_reasons", []),
                },
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
