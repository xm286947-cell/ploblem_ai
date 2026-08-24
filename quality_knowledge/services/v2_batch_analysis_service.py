"""Bounded issue-level concurrency for native V2 analysis."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from quality_knowledge.services.v2_analysis_service import V2AnalysisService

class V2BatchAnalysisError(RuntimeError):
    """Stable validation errors for a batch analysis request."""

class V2BatchAnalysisService:
    MIN_CONCURRENCY = 1
    MAX_CONCURRENCY = 4
    MAX_ISSUES = 500

    def __init__(self, repository: Any, stage_runner: Any | None):
        self.repository = repository
        self.stage_runner = stage_runner

    def run(self, knowledge_ids: list[str], *, concurrency: int = 2, request: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.stage_runner is None:
            raise V2BatchAnalysisError("ANALYSIS_RUNNER_NOT_CONFIGURED")
        try:
            concurrency = int(concurrency)
        except (TypeError, ValueError) as error:
            raise V2BatchAnalysisError("BATCH_ANALYSIS_CONCURRENCY_INVALID") from error
        if not self.MIN_CONCURRENCY <= concurrency <= self.MAX_CONCURRENCY:
            raise V2BatchAnalysisError("BATCH_ANALYSIS_CONCURRENCY_INVALID")
        if not isinstance(knowledge_ids, list):
            raise V2BatchAnalysisError("BATCH_ANALYSIS_IDS_INVALID")
        unique_ids = list(dict.fromkeys(
            str(item).strip() for item in knowledge_ids if item is not None and str(item).strip()
        ))
        if not unique_ids:
            raise V2BatchAnalysisError("BATCH_ANALYSIS_EMPTY")
        if len(unique_ids) > self.MAX_ISSUES:
            raise V2BatchAnalysisError("BATCH_ANALYSIS_LIMIT_EXCEEDED")
        shared_request = dict(request or {})
        indexed: dict[str, dict[str, Any]] = {}

        def analyze_one(knowledge_id: str) -> dict[str, Any]:
            try:
                envelope = V2AnalysisService(self.repository, self.stage_runner).run(knowledge_id, dict(shared_request))
                outcome = {
                    "COMPLETED": "SUCCEEDED",
                    "PARTIAL_FAILED": "PARTIAL",
                    "FAILED": "FAILED",
                }.get(envelope.status, "FAILED")
                return {"knowledge_id": knowledge_id, "outcome": outcome, "analysis_status": envelope.status,
                        "analysis_set_id": envelope.analysis_set_id, "warnings": list(envelope.warnings)}
            except Exception as error:  # one issue must not cancel the other workers
                return {"knowledge_id": knowledge_id, "outcome": "FAILED", "error": str(error) or error.__class__.__name__}

        workers = min(concurrency, len(unique_ids))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="issue-analysis") as executor:
            futures = {executor.submit(analyze_one, knowledge_id): knowledge_id for knowledge_id in unique_ids}
            for future in as_completed(futures):
                knowledge_id = futures[future]
                indexed[knowledge_id] = future.result()
        items = [indexed[knowledge_id] for knowledge_id in unique_ids]
        succeeded = sum(item["outcome"] == "SUCCEEDED" for item in items)
        partial = sum(item["outcome"] == "PARTIAL" for item in items)
        failed = sum(item["outcome"] == "FAILED" for item in items)
        outcome = "COMPLETED" if succeeded == len(items) else ("FAILED" if failed == len(items) else "PARTIAL_FAILED")
        return {"outcome": outcome, "total": len(items), "succeeded": succeeded, "partial": partial,
                "failed": failed, "concurrency": concurrency, "items": items}
