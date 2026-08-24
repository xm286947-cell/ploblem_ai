from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json
import time

from builder.m72_normalizer_runner import run_m72_normalizer
from builder.m72_ai_runner import run_m72_ai
from builder.m72_standard_builder_runner import run_m72_standard_builder
from parser.common import write_json

PIPELINE_VERSION = "M7.2-P1"
_VALID_STAGES = ("normalize", "ai", "build")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _query_ids(root: Path, query_id: str | None, from_stage: str) -> list[str]:
    if query_id:
        return [query_id]
    source_by_stage = {
        "normalize": root / "knowledge/raw_query",
        "ai": root / "knowledge/normalized_query",
        "build": root / "knowledge/enriched_query",
    }
    return sorted(path.stem for path in source_by_stage[from_stage].glob("*.json"))


def _query_result(root: Path, query_id: str) -> dict[str, Any]:
    normalized = _load_json(root / "knowledge/normalized_query" / f"{query_id}.json")
    enriched = _load_json(root / "knowledge/enriched_query" / f"{query_id}.json")
    standard = _load_json(root / "knowledge/standard_query" / f"{query_id}.json")

    result = {
        "query_id": query_id,
        "normalized_status": (normalized or {}).get("normalize_status", "NOT_GENERATED"),
        "enriched_status": (enriched or {}).get("enrich_status", "NOT_GENERATED"),
        "standard_status": (standard or {}).get("build_status", "NOT_GENERATED"),
        "standard_query_generated": standard is not None,
    }
    if standard is not None:
        result["quality_flags"] = standard.get("quality_flags", [])
    return result


def run_m72_pipeline(
    root: Path,
    query_id: str | None = None,
    overwrite: bool = False,
    mock: bool = False,
    skip_ai: bool = False,
    from_stage: str = "normalize",
) -> dict[str, Any]:
    """Run M7.2 from the requested stage through Standard Query build.

    Stages are persisted independently. A failed query does not stop other queries.
    Existing outputs are reused unless ``overwrite`` is true.
    """
    if from_stage not in _VALID_STAGES:
        raise ValueError(f"from_stage必须是: {', '.join(_VALID_STAGES)}")

    started = time.perf_counter()
    ids = _query_ids(root, query_id, from_stage)
    summary: dict[str, Any] = {
        "pipeline_version": PIPELINE_VERSION,
        "from_stage": from_stage,
        "query_id": query_id or "",
        "started_at": _now(),
        "completed_at": "",
        "elapsed_seconds": 0.0,
        "query_count": len(ids),
        "stages": {},
        "queries": [],
        "success": 0,
        "partial_success": 0,
        "failed": 0,
    }

    if from_stage == "normalize":
        summary["stages"]["normalize"] = run_m72_normalizer(root, query_id=query_id, overwrite=overwrite)
    if from_stage in ("normalize", "ai"):
        summary["stages"]["ai"] = run_m72_ai(
            root,
            query_id=query_id,
            overwrite=overwrite,
            mock=mock,
            skip_ai=skip_ai,
        )
    summary["stages"]["build"] = run_m72_standard_builder(root, query_id=query_id, overwrite=overwrite)

    # Re-evaluate IDs because an initial directory may have been empty while a stage generated files.
    if not ids and query_id:
        ids = [query_id]
    elif not ids:
        ids = sorted(path.stem for path in (root / "knowledge/standard_query").glob("*.json"))
    summary["query_count"] = len(ids)

    for qid in ids:
        result = _query_result(root, qid)
        summary["queries"].append(result)
        if not result["standard_query_generated"]:
            summary["failed"] += 1
        elif result["standard_status"] == "SUCCESS":
            summary["success"] += 1
        else:
            summary["partial_success"] += 1

    summary["completed_at"] = _now()
    summary["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    write_json(root / "output/logs/m72_pipeline_summary.json", summary)
    return summary
