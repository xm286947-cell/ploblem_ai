"""Observable in-process jobs for native V2 batch analysis."""
from __future__ import annotations

import copy
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from typing import Any

from quality_knowledge.services.v2_analysis_service import STAGES, V2AnalysisService
from quality_knowledge.services.v2_batch_analysis_service import V2BatchAnalysisError


class V2BatchAnalysisJobManager:
    """Run bounded analysis jobs while exposing issue and stage progress."""

    MIN_CONCURRENCY = 1
    MAX_CONCURRENCY = 4
    MAX_ISSUES = 500
    MAX_RETAINED_JOBS = 20

    def __init__(self, repository: Any, stage_runner: Any | None):
        self.repository = repository
        self.stage_runner = stage_runner
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def start(
        self,
        knowledge_ids: list[str],
        *,
        concurrency: int = 2,
        request: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ids, workers = self._validate(knowledge_ids, concurrency)
        job_id = f"AJ-{uuid.uuid4().hex}"
        now = self._now()
        job = {
            "job_id": job_id,
            "status": "QUEUED",
            "total": len(ids),
            "completed": 0,
            "succeeded": 0,
            "partial": 0,
            "failed": 0,
            "concurrency": workers,
            "created_at": now,
            "started_at": None,
            "completed_at": None,
            "items": [
                {
                    "knowledge_id": knowledge_id,
                    "position": index + 1,
                    "status": "QUEUED",
                    "analysis_status": "NOT_STARTED",
                    "current_stage": None,
                    "stages": {stage: "QUEUED" for stage in STAGES},
                    "analysis_set_id": None,
                    "warnings": [],
                    "error": None,
                }
                for index, knowledge_id in enumerate(ids)
            ],
        }
        with self._lock:
            self._jobs[job_id] = job
            self._trim_jobs()
        threading.Thread(
            target=self._run,
            args=(job_id, ids, dict(request or {})),
            name=f"analysis-job-{job_id[-8:]}",
            daemon=True,
        ).start()
        return self.get(job_id)

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(str(job_id))
            if job is None:
                raise V2BatchAnalysisError("ANALYSIS_JOB_NOT_FOUND")
            return copy.deepcopy(job)

    def _validate(self, knowledge_ids: list[str], concurrency: int) -> tuple[list[str], int]:
        if self.stage_runner is None:
            raise V2BatchAnalysisError("ANALYSIS_RUNNER_NOT_CONFIGURED")
        try:
            workers = int(concurrency)
        except (TypeError, ValueError) as error:
            raise V2BatchAnalysisError("BATCH_ANALYSIS_CONCURRENCY_INVALID") from error
        if not self.MIN_CONCURRENCY <= workers <= self.MAX_CONCURRENCY:
            raise V2BatchAnalysisError("BATCH_ANALYSIS_CONCURRENCY_INVALID")
        if not isinstance(knowledge_ids, list):
            raise V2BatchAnalysisError("BATCH_ANALYSIS_IDS_INVALID")
        ids = list(dict.fromkeys(
            str(item).strip() for item in knowledge_ids if item is not None and str(item).strip()
        ))
        if not ids:
            raise V2BatchAnalysisError("BATCH_ANALYSIS_EMPTY")
        if len(ids) > self.MAX_ISSUES:
            raise V2BatchAnalysisError("BATCH_ANALYSIS_LIMIT_EXCEEDED")
        return ids, min(workers, len(ids))

    def _run(self, job_id: str, ids: list[str], request: dict[str, Any]) -> None:
        self._update_job(job_id, status="RUNNING", started_at=self._now())
        with ThreadPoolExecutor(
            max_workers=self.get(job_id)["concurrency"],
            thread_name_prefix="observable-analysis",
        ) as executor:
            futures = {
                executor.submit(self._analyze_one, job_id, index, knowledge_id, request): index
                for index, knowledge_id in enumerate(ids)
            }
            for future in as_completed(futures):
                index = futures[future]
                try:
                    result = future.result()
                except Exception as error:  # defensive: worker already isolates domain failures
                    result = {"status": "FAILED", "analysis_status": "FAILED", "error": str(error)}
                self._finish_item(job_id, index, result)
        with self._lock:
            job = self._jobs[job_id]
            if job["failed"] == job["total"]:
                status = "FAILED"
            elif job["failed"] or job["partial"]:
                status = "PARTIAL_FAILED"
            else:
                status = "COMPLETED"
            job["status"] = status
            job["completed_at"] = self._now()

    def _analyze_one(
        self,
        job_id: str,
        index: int,
        knowledge_id: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        self._update_item(job_id, index, status="RUNNING", analysis_status="RUNNING")

        def progress(event: dict[str, Any]) -> None:
            stage = str(event.get("stage") or "")
            stage_status = str(event.get("status") or "RUNNING")
            values: dict[str, Any] = {"current_stage": stage}
            if event.get("error"):
                values["error"] = str(event["error"])
            self._update_item(job_id, index, stage=stage, stage_status=stage_status, **values)

        try:
            envelope = V2AnalysisService(
                self.repository,
                self.stage_runner,
                progress_callback=progress,
            ).run(knowledge_id, dict(request))
            outcome = {
                "COMPLETED": "COMPLETED",
                "PARTIAL_FAILED": "PARTIAL",
                "FAILED": "FAILED",
            }.get(envelope.status, "FAILED")
            return {
                "status": outcome,
                "analysis_status": envelope.status,
                "analysis_set_id": envelope.analysis_set_id,
                "warnings": list(envelope.warnings),
            }
        except Exception as error:
            return {
                "status": "FAILED",
                "analysis_status": "FAILED",
                "error": str(error) or error.__class__.__name__,
            }

    def _finish_item(self, job_id: str, index: int, result: dict[str, Any]) -> None:
        with self._lock:
            job = self._jobs[job_id]
            item = job["items"][index]
            item.update(result)
            item["current_stage"] = None
            for stage, status in list(item["stages"].items()):
                if status == "QUEUED":
                    item["stages"][stage] = "COMPLETED" if result["status"] == "COMPLETED" else "NOT_RUN"
            job["completed"] += 1
            if result["status"] == "COMPLETED":
                job["succeeded"] += 1
            elif result["status"] == "PARTIAL":
                job["partial"] += 1
            else:
                job["failed"] += 1

    def _update_job(self, job_id: str, **values: Any) -> None:
        with self._lock:
            self._jobs[job_id].update(values)

    def _update_item(
        self,
        job_id: str,
        index: int,
        *,
        stage: str = "",
        stage_status: str = "",
        **values: Any,
    ) -> None:
        with self._lock:
            item = self._jobs[job_id]["items"][index]
            item.update(values)
            if stage in item["stages"] and stage_status:
                item["stages"][stage] = stage_status

    def _trim_jobs(self) -> None:
        if len(self._jobs) <= self.MAX_RETAINED_JOBS:
            return
        completed = [
            job_id for job_id, job in self._jobs.items()
            if job["status"] in {"COMPLETED", "PARTIAL_FAILED", "FAILED"}
        ]
        for job_id in completed[: max(0, len(self._jobs) - self.MAX_RETAINED_JOBS)]:
            self._jobs.pop(job_id, None)

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()
