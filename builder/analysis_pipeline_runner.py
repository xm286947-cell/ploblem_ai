from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
import time

from builder.m71_query_runner import run_m71_query
from builder.query_enricher import run_m72_pipeline
from builder.m73_profile_runner import run_m73_profile
from builder.m73_retriever_runner import run_m73_retrieve
from builder.m81_candidate_runner import run_m81_load
from builder.m82_similarity_runner import run_m82_similarity
from builder.m83_solution_runner import run_m83_solution
from builder.m84_repeat_runner import run_m84_decision
from builder.m85_delivery_runner import run_m85_delivery
from parser.common import write_json

PIPELINE_VERSION = "V2.3-M5"
STAGES = ("parse", "enrich", "profile", "retrieve", "load", "similarity", "solution", "decision", "delivery")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _query_ids(root: Path, query_id: str | None) -> list[str]:
    if query_id:
        return [query_id]
    return sorted(path.stem for path in (root / "knowledge/raw_query").glob("*.json"))


def _failed_count(result: dict[str, Any]) -> int:
    for key in ("failed", "failed_count"):
        value = result.get(key)
        if isinstance(value, int):
            return value
    return 0


def _run_stage(
    name: str,
    fn: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    started = time.perf_counter()
    record: dict[str, Any] = {
        "stage": name,
        "status": "RUNNING",
        "started_at": _now(),
        "completed_at": "",
        "elapsed_seconds": 0.0,
        "result": {},
        "error": "",
    }
    try:
        result = fn()
        record["result"] = result
        record["status"] = "FAILED" if _failed_count(result) > 0 else "SUCCESS"
    except Exception as exc:
        record["status"] = "FAILED"
        record["error"] = str(exc)
    record["completed_at"] = _now()
    record["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    return record


def run_analysis_pipeline(
    project_root: str | Path,
    input_path: str | Path | None = None,
    query_id: str | None = None,
    from_stage: str = "parse",
    top_k: int | None = None,
    overwrite: bool = False,
    mock: bool = False,
    skip_ai: bool = False,
) -> dict[str, Any]:
    """Run the complete new-query analysis pipeline.

    Each query is isolated: a failure stops only that query and is persisted in the
    pipeline summary. Existing stage runners remain the single source of business
    behavior; this module only orchestrates them.
    """
    if from_stage not in STAGES:
        raise ValueError(f"from_stage必须是: {', '.join(STAGES)}")

    root = Path(project_root).resolve()
    started = time.perf_counter()
    summary: dict[str, Any] = {
        "pipeline_version": PIPELINE_VERSION,
        "pipeline": "M7.1->M7.2->M7.3->M8.1->M8.2->M8.3->M8.4->M8.5",
        "from_stage": from_stage,
        "query_id": query_id or "",
        "started_at": _now(),
        "completed_at": "",
        "elapsed_seconds": 0.0,
        "status": "RUNNING",
        "query_count": 0,
        "success": 0,
        "failed": 0,
        "parse": None,
        "queries": [],
    }

    start_index = STAGES.index(from_stage)
    if start_index == 0:
        summary["parse"] = _run_stage(
            "parse",
            lambda: run_m71_query(root, input_path=str(input_path) if input_path else None, overwrite=overwrite),
        )
        if summary["parse"]["status"] == "FAILED":
            summary["status"] = "FAILED"
            summary["completed_at"] = _now()
            summary["elapsed_seconds"] = round(time.perf_counter() - started, 3)
            write_json(root / "output/logs/analysis_pipeline_summary.json", summary)
            return summary

    ids = _query_ids(root, query_id)
    summary["query_count"] = len(ids)
    if not ids:
        summary["status"] = "FAILED"
        summary["failed"] = 1
        summary["queries"].append({"query_id": query_id or "", "status": "FAILED", "error": "RAW_QUERY_NOT_FOUND", "stages": []})
    else:
        stage_fns: list[tuple[str, Callable[[str], dict[str, Any]]]] = [
            ("enrich", lambda qid: run_m72_pipeline(root, query_id=qid, overwrite=overwrite, mock=mock, skip_ai=skip_ai)),
            ("profile", lambda qid: run_m73_profile(root, query_id=qid, overwrite=overwrite)),
            ("retrieve", lambda qid: run_m73_retrieve(root, query_id=qid, top_k=top_k, overwrite=overwrite)),
            ("load", lambda qid: run_m81_load(root, query_id=qid, top_k=top_k, overwrite=overwrite)),
            ("similarity", lambda qid: run_m82_similarity(root, query_id=qid, overwrite=overwrite, mock=mock, skip_ai=skip_ai)),
            ("solution", lambda qid: run_m83_solution(root, query_id=qid, overwrite=overwrite, mock=mock, skip_ai=skip_ai)),
            ("decision", lambda qid: run_m84_decision(root, query_id=qid, overwrite=overwrite, mock=mock, skip_ai=skip_ai)),
            ("delivery", lambda qid: run_m85_delivery(root, query_id=qid, overwrite=overwrite)),
        ]
        stage_fns = [(name, fn) for name, fn in stage_fns if STAGES.index(name) >= start_index]

        for qid in ids:
            query_record: dict[str, Any] = {
                "query_id": qid,
                "status": "RUNNING",
                "failed_stage": "",
                "error": "",
                "stages": [],
            }
            for name, fn in stage_fns:
                stage_record = _run_stage(name, lambda fn=fn, qid=qid: fn(qid))
                query_record["stages"].append(stage_record)
                if stage_record["status"] == "FAILED":
                    query_record["status"] = "FAILED"
                    query_record["failed_stage"] = name
                    errors = stage_record.get("result", {}).get("errors", [])
                    detail = errors[0].get("error", "") if errors and isinstance(errors[0], dict) else ""
                    query_record["error"] = stage_record["error"] or detail or "STAGE_RESULT_FAILED"
                    break
            if query_record["status"] == "RUNNING":
                query_record["status"] = "SUCCESS"
                summary["success"] += 1
            else:
                summary["failed"] += 1
            summary["queries"].append(query_record)

        summary["status"] = "SUCCESS" if summary["failed"] == 0 else ("PARTIAL_SUCCESS" if summary["success"] else "FAILED")

    summary["completed_at"] = _now()
    summary["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    write_json(root / "output/logs/analysis_pipeline_summary.json", summary)
    return summary
