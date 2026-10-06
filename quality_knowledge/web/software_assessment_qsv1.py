"""Browser orchestration for the existing Software Assessment -> QSV1 chain.

This module is glue only: source snapshots, Bundle V1, the mature Reverse
Quality bridge, Candidate V1, and QSV1 workflow storage remain their owners.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from typing import Any
from urllib.parse import unquote, urlsplit

from fastapi import APIRouter, BackgroundTasks, HTTPException

from quality_knowledge.quality_scenario_candidate_v1_service import CandidateV1Service
from quality_knowledge.quality_scenario_v1 import ScenarioTriggerSource
from quality_knowledge.quality_scenario_v1_store import SQLiteQualityScenarioV1Repository
from quality_knowledge.reverse_quality_bundle_adapter import ReverseQualityInputAdapter
from quality_knowledge.scenario_source_bundle_v1 import build_scenario_source_bundle_v1


class SoftwareAssessmentQSV1TaskStore:
    """Durable browser-orchestration metadata; Runtime execution remains Runtime-owned."""

    def __init__(self, db_path: str):
        self.db_path = str(db_path)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS quality_scenario_generation_task_v1(
                       task_id TEXT PRIMARY KEY,
                       task_json TEXT NOT NULL,
                       updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                   )"""
            )

    def save(self, task: dict[str, Any]) -> None:
        payload = json.dumps(
            task, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        )
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO quality_scenario_generation_task_v1(task_id,task_json)
                   VALUES(?,?)
                   ON CONFLICT(task_id) DO UPDATE SET
                     task_json=excluded.task_json,
                     updated_at=CURRENT_TIMESTAMP""",
                (str(task["task_id"]), payload),
            )

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT task_json FROM quality_scenario_generation_task_v1 WHERE task_id=?",
                (str(task_id),),
            ).fetchone()
        if row is None:
            return None
        value = json.loads(row["task_json"])
        return value if isinstance(value, dict) else None


class SoftwareAssessmentQSV1Flow:
    """State translation for the mature workbench, with no second business store."""

    def __init__(self, generation_service, scenario_repository, qsv1_db_path: str):
        self.generation = generation_service
        self.scenarios = scenario_repository
        self.bundle_store = generation_service.source_bundle_snapshots
        self.qsv1 = SQLiteQualityScenarioV1Repository(qsv1_db_path)
        self.candidates = CandidateV1Service(self.qsv1)
        self.task_store = SoftwareAssessmentQSV1TaskStore(qsv1_db_path)
        self._tasks: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def _store_task(self, task: dict[str, Any]) -> None:
        with self._lock:
            self._tasks[str(task["task_id"])] = task
        self.task_store.save(task)

    def _load_task(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            cached = self._tasks.get(task_id)
        if cached is not None:
            return cached
        persisted = self.task_store.get(task_id)
        if persisted is None:
            return None

        # A process restart can leave a persisted item at ANALYZING even though
        # no worker is still alive. Convert only those orphaned in-flight items
        # into an explicit retryable failure while preserving the frozen Bundle.
        interrupted = False
        for item in persisted.get("items") or []:
            if item.get("state") == "ANALYZING":
                item["state"] = "PROVIDER_FAILED"
                item["error"] = "GENERATION_INTERRUPTED_BY_PROCESS_RESTART"
                item["created"] = None
                interrupted = True
        if persisted.get("state") == "ANALYZING":
            persisted["state"] = "PARTIAL"
            interrupted = True

        with self._lock:
            self._tasks[task_id] = persisted
        if interrupted:
            self.task_store.save(persisted)
        return persisted

    @staticmethod
    def _validate_trigger(trigger_source: str, trigger_reason: str) -> tuple[str, str]:
        try:
            source = ScenarioTriggerSource(trigger_source).value
        except (TypeError, ValueError) as error:
            raise ValueError("QSV1_TRIGGER_SOURCE_REQUIRED") from error
        reason = str(trigger_reason or "").strip()
        if not reason:
            raise ValueError("QSV1_TRIGGER_REASON_REQUIRED")
        return source, reason

    def _selected_rows(self, material_ids: list[str]) -> list[dict[str, Any]]:
        ids = list(dict.fromkeys(str(item or "").strip() for item in material_ids if str(item or "").strip()))
        if not ids or len(ids) > 20:
            raise ValueError("SOFTWARE_ASSESSMENT_SELECTION_REQUIRED")
        from quality_knowledge.scenario_sources import operation_records

        rows = operation_records(self.generation, selected_ids=ids)
        by_id = {str(row.get("source_material_id") or ""): row for row in rows}
        if set(by_id) != set(ids):
            raise ValueError("SOFTWARE_ASSESSMENT_SOURCE_BINDING_FAILED")
        return [by_id[item] for item in ids]

    def _preview_bundle(self, row: dict[str, Any], trigger_source: str, trigger_reason: str) -> dict[str, Any]:
        return build_scenario_source_bundle_v1(
            row,
            evidence_repository=self.generation.materials,
            trigger_source=trigger_source,
            trigger_reason=trigger_reason,
        )

    @staticmethod
    def _bundle_key_from_scenario(scenario) -> tuple[str, str] | None:
        for evidence in scenario.evidence_refs:
            if evidence.evidence_type != "QSV1_PRODUCTION_PROVENANCE":
                continue
            parsed = urlsplit(evidence.content_ref)
            if parsed.scheme != "scenario-source-bundle" or not parsed.netloc:
                continue
            revision = unquote(parsed.path.lstrip("/"))
            if revision:
                return unquote(parsed.netloc), revision
        return None

    def _existing_for(self, bundle: dict[str, Any], trigger_source: str, trigger_reason: str):
        selected_id = str(bundle.get("primary_source_id") or "")
        previous_revision = False
        for scenario in self.qsv1.list():
            refs = {item.source_id for item in scenario.source_problem_refs if item.source_type == "SCENARIO_SOURCE_BUNDLE"}
            selected_refs = {item.source_id for item in scenario.source_problem_refs if item.source_type == "SELECTED_ISSUE"}
            if selected_id not in selected_refs or scenario.trigger_source is None:
                continue
            key = self._bundle_key_from_scenario(scenario)
            if key and key == (str(bundle.get("bundle_id") or ""), str(bundle.get("bundle_revision") or "")):
                if scenario.trigger_source.value == trigger_source and scenario.trigger_reason.strip() == trigger_reason:
                    return scenario, "EXISTING_CANDIDATE"
            elif refs and scenario.trigger_source.value == trigger_source and scenario.trigger_reason.strip() == trigger_reason:
                previous_revision = True
        return (None, "SOURCE_CHANGED_REANALYSIS_AVAILABLE" if previous_revision else "READY")

    @staticmethod
    def _completeness(bundle: dict[str, Any]) -> dict[str, Any]:
        return {
            "selected_issue": bundle.get("selected_issue") or {},
            "bundle_id": bundle.get("bundle_id") or "",
            "bundle_revision": bundle.get("bundle_revision") or "",
            "source_snapshot_id": "" if (bundle.get("snapshot_metadata") or {}).get("preview_only") else (bundle.get("snapshot_metadata") or {}).get("snapshot_id") or "",
            "source_status": bundle.get("source_status") or {},
            "warnings": bundle.get("warnings") or [],
            "missing_information": bundle.get("missing_information") or [],
        }

    def _preview_rows(self, rows, trigger_source: str, trigger_reason: str) -> dict[str, Any]:
        items = []
        for row in rows:
            try:
                preview_row = dict(row)
                if not preview_row.get("snapshot_metadata"):
                    preview_row["snapshot_metadata"] = {
                        "snapshot_id": "PREVIEW_ONLY",
                        "builder_version": "software-assessment-browser-preview/v1",
                        "preview_only": True,
                    }
                bundle = self._preview_bundle(preview_row, trigger_source, trigger_reason)
                state = "INFORMATION_REQUIRED" if any(
                    item.get("severity") == "BLOCKER" for item in bundle.get("missing_information") or []
                ) else "READY"
                try:
                    ReverseQualityInputAdapter().adapt(bundle)
                except ValueError as error:
                    state = "INFORMATION_REQUIRED"
                    bundle.setdefault("missing_information", []).append({
                        "code": str(error), "severity": "BLOCKER", "message": str(error),
                    })
                existing, existing_state = self._existing_for(bundle, trigger_source, trigger_reason)
                if existing is not None:
                    state = existing_state
                elif state == "READY":
                    state = existing_state
                items.append({
                    "state": state,
                    "source_material_id": row.get("source_material_id") or "",
                    "bundle": self._completeness(bundle),
                    "scenario": existing.model_dump(mode="json") if existing else None,
                })
            except ValueError as error:
                code = str(error)
                items.append({
                    "state": "SOURCE_BINDING_FAILED" if any(token in code for token in ("SOURCE", "LOCATOR", "SOFTWARE_ASSESSMENT")) else "INFORMATION_REQUIRED",
                    "source_material_id": row.get("source_material_id") or "",
                    "bundle": None,
                    "scenario": None,
                    "error": code,
                })
        return {"items": items, "total": len(items), "ready_count": sum(item["state"] == "READY" for item in items)}

    def preview(self, material_ids: list[str], trigger_source: str, trigger_reason: str) -> dict[str, Any]:
        trigger_source, trigger_reason = self._validate_trigger(trigger_source, trigger_reason)
        return self._preview_rows(self._selected_rows(material_ids), trigger_source, trigger_reason)

    def start(self, material_ids: list[str], trigger_source: str, trigger_reason: str) -> dict[str, Any]:
        trigger_source, trigger_reason = self._validate_trigger(trigger_source, trigger_reason)
        rows = self._selected_rows(material_ids)
        preview = self._preview_rows(rows, trigger_source, trigger_reason)
        preview_by_id = {item["source_material_id"]: item for item in preview["items"]}
        generation_id = "QSF-" + uuid.uuid4().hex
        self.generation.save_source_snapshot(generation_id, rows)
        frozen_rows = self.generation.source_snapshot(generation_id)
        frozen_by_id = {str(row.get("source_material_id") or ""): row for row in frozen_rows}
        bundle_items = []
        for row in rows:
            material_id = str(row.get("source_material_id") or "")
            view = preview_by_id.get(material_id) or {}
            state = view.get("state") or "SOURCE_BINDING_FAILED"
            if state in {"INFORMATION_REQUIRED", "SOURCE_BINDING_FAILED"}:
                bundle_items.append({
                    "state": state, "source_material_id": material_id,
                    "bundle": view.get("bundle"), "scenario": None,
                    "error": view.get("error") or "SOURCE_COMPLETENESS_BLOCKED",
                })
                continue
            frozen = frozen_by_id.get(material_id)
            if not frozen:
                bundle_items.append({"state": "SOURCE_BINDING_FAILED", "source_material_id": material_id, "error": "FROZEN_SOURCE_SNAPSHOT_NOT_FOUND"})
                continue
            try:
                bundle = self._preview_bundle(frozen, trigger_source, trigger_reason)
                expected = (view.get("bundle") or {}).get("bundle_revision")
                if expected and bundle.get("bundle_revision") != expected:
                    raise ValueError("SOURCE_CHANGED_BEFORE_BUNDLE_FREEZE")
                saved = self.bundle_store.get(bundle["bundle_id"], bundle["bundle_revision"])
                if saved is None:
                    self.bundle_store.save(bundle)
                    saved = bundle
                existing, existing_state = self._existing_for(saved, trigger_source, trigger_reason)
                bundle_items.append({
                    "state": existing_state if existing else "ANALYZING",
                    "source_material_id": material_id,
                    "bundle_key": {"bundle_id": saved["bundle_id"], "bundle_revision": saved["bundle_revision"]},
                    "bundle": self._completeness(saved),
                    "scenario": existing.model_dump(mode="json") if existing else None,
                    "created": False if existing else None,
                })
            except ValueError as error:
                code = str(error)
                bundle_items.append({
                    "state": "SOURCE_BINDING_FAILED" if any(token in code for token in ("SOURCE", "LOCATOR", "SOFTWARE_ASSESSMENT")) else "INFORMATION_REQUIRED",
                    "source_material_id": material_id, "error": code,
                })
        task_id = "QSFAST-" + uuid.uuid4().hex[:16]
        task = {
            "task_id": task_id, "state": "ANALYZING", "progress": {"completed": 0, "total": len(bundle_items)},
            "generation_id": generation_id, "trigger_source": trigger_source, "trigger_reason": trigger_reason,
            "items": bundle_items,
        }
        self._store_task(task)
        return self.get_task(task_id)

    def run_task(self, task_id: str) -> None:
        task = self._load_task(task_id)
        if not task:
            return
        completed = 0
        for item in task["items"]:
            if item.get("state") in {"INFORMATION_REQUIRED", "SOURCE_BINDING_FAILED", "EXISTING_CANDIDATE"}:
                completed += 1
                with self._lock:
                    task["progress"] = {"completed": completed, "total": len(task["items"])}
                self.task_store.save(task)
                continue
            key = item.get("bundle_key")
            try:
                bundle = self.bundle_store.get(key["bundle_id"], key["bundle_revision"]) if key else None
                if bundle is None:
                    raise ValueError("SCENARIO_SOURCE_BUNDLE_SNAPSHOT_NOT_FOUND")
                product_code = str((bundle.get("selected_issue") or {}).get("product_code") or "").strip()
                if not product_code:
                    item.update(state="INFORMATION_REQUIRED", error="BUNDLE_PRODUCT_CODE_REQUIRED")
                    completed += 1
                    continue
                taxonomy = self.scenarios.taxonomy_active(product_code)
                if not taxonomy:
                    item.update(state="INFORMATION_REQUIRED", error="REVERSE_QUALITY_TAXONOMY_NOT_FOUND")
                    completed += 1
                    continue
                reverse_result = self.generation.reverse_quality_from_bundle(bundle, taxonomy=taxonomy)
                produced = self.candidates.create_from_reverse(
                    reverse_result, taxonomy,
                    trigger_source=task["trigger_source"],
                    trigger_reason=task["trigger_reason"],
                    created_by="SOFTWARE_ASSESSMENT_WORKBENCH",
                )
                persisted = self.qsv1.get(produced.scenario.scenario_id)
                if persisted is None:
                    raise ValueError("QSV1_PERSISTED_SCENARIO_NOT_FOUND")
                item.update(
                    state="EXISTING_CANDIDATE" if not produced.created else "CANDIDATE_CREATED",
                    scenario=persisted.model_dump(mode="json"), created=produced.created,
                )
            except ValueError as error:
                code = str(error)
                if any(token in code for token in ("SOURCE", "LOCATOR", "BUNDLE_SNAPSHOT")):
                    item.update(state="SOURCE_BINDING_FAILED", error=code)
                elif "人工" in code or "CONFIRMED" in code or "REVIEWED" in code:
                    item.update(state="INFORMATION_REQUIRED", error=code)
                elif code.startswith("INFORMATION_REQUIRED") or code.startswith("BUNDLE_") or code.startswith("QSV1_BUNDLE"):
                    item.update(state="INFORMATION_REQUIRED", error=code)
                else:
                    item.update(state="PROVIDER_FAILED", error=code)
            except Exception:  # Keep provider/runtime details out of the browser response.
                item.update(state="PROVIDER_FAILED", error="REVERSE_QUALITY_PROVIDER_FAILED")
            completed += 1
            with self._lock:
                task["progress"] = {"completed": completed, "total": len(task["items"])}
            self.task_store.save(task)
        with self._lock:
            states = {item.get("state") for item in task["items"]}
            task["state"] = "COMPLETED" if not states.intersection({"ANALYZING", "PROVIDER_FAILED", "SOURCE_BINDING_FAILED", "INFORMATION_REQUIRED"}) else "PARTIAL"
        self.task_store.save(task)

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        task = self._load_task(task_id)
        if task is None:
            return None
        return {
            key: value for key, value in task.items()
            if key not in {"generation_id"}
        }

    def retry(self, task_id: str) -> dict[str, Any]:
        source = self._load_task(task_id)
        if source is None:
            raise ValueError("SOFTWARE_ASSESSMENT_TASK_NOT_FOUND")
        retry_items = [item for item in source["items"] if item.get("state") == "PROVIDER_FAILED"]
        if not retry_items:
            raise ValueError("SOFTWARE_ASSESSMENT_NO_RETRYABLE_ITEMS")
        new_task_id = "QSFAST-" + uuid.uuid4().hex[:16]
        task = {
            "task_id": new_task_id, "state": "ANALYZING",
            "progress": {"completed": 0, "total": len(retry_items)},
            "trigger_source": source["trigger_source"], "trigger_reason": source["trigger_reason"],
            "items": [dict(item, state="ANALYZING", error="", created=None) for item in retry_items],
        }
        self._store_task(task)
        return self.get_task(new_task_id)


def create_software_assessment_qsv1_router(generation_service, scenario_repository, qsv1_db_path: str) -> APIRouter:
    router = APIRouter(prefix="/api/v2/software-assessment/quality-scenario", tags=["Software Assessment QSV1 Browser Flow"])
    flow = SoftwareAssessmentQSV1Flow(generation_service, scenario_repository, qsv1_db_path)

    @router.post("/preview")
    def preview(payload: dict[str, Any]):
        try:
            return flow.preview(
                payload.get("material_ids") or [],
                str(payload.get("trigger_source") or ""),
                str(payload.get("trigger_reason") or ""),
            )
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

    @router.post("/generations")
    def start_generation(payload: dict[str, Any], background_tasks: BackgroundTasks):
        try:
            task = flow.start(
                payload.get("material_ids") or [],
                str(payload.get("trigger_source") or ""),
                str(payload.get("trigger_reason") or ""),
            )
            background_tasks.add_task(flow.run_task, task["task_id"])
            return task
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

    @router.get("/generations/{task_id}")
    def task_status(task_id: str):
        item = flow.get_task(task_id)
        if item is None:
            raise HTTPException(404, "SOFTWARE_ASSESSMENT_TASK_NOT_FOUND")
        return item

    @router.post("/generations/{task_id}/retry")
    def retry_generation(task_id: str, background_tasks: BackgroundTasks):
        try:
            task = flow.retry(task_id)
            background_tasks.add_task(flow.run_task, task["task_id"])
            return task
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

    return router


__all__ = ["SoftwareAssessmentQSV1Flow", "create_software_assessment_qsv1_router"]
