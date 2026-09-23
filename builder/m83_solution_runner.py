from __future__ import annotations

import time
import yaml
from pathlib import Path
from typing import Any

from builder.parallel_execution import load_parallel_execution_config, ordered_map
from builder.solution_analyzer import SolutionAnalyzer
from builder.validators import validate_json
from parser.common import write_json
from repositories import JsonArtifactRepository
from services import KnowledgeService


def _solution_execution_config(root: Path) -> tuple[int, float]:
    data = yaml.safe_load((root / "config/model.yaml").read_text(encoding="utf-8")) or {}
    cfg = data.get("solution_ai") or {}
    try:
        top_n = max(0, int(cfg.get("candidate_top_n", 3)))
    except (TypeError, ValueError):
        top_n = 3
    try:
        min_score = float(cfg.get("min_similarity_score", 0))
    except (TypeError, ValueError):
        min_score = 0.0
    return top_n, min_score


def _similarity_score(service: KnowledgeService, qid: str, cid: str) -> float:
    artifacts = service.load_analysis_artifacts(qid, cid)
    analysis = (artifacts.similarity_analysis or {}).get("analysis") or {}
    try:
        return float(analysis.get("overall_score") or 0)
    except (TypeError, ValueError):
        return 0.0


def run_m83_solution(root: Path, query_id: str | None = None, case_id: str | None = None,
                     overwrite: bool = False, mock: bool = False, skip_ai: bool = False) -> dict[str, Any]:
    output_root = root / "knowledge/solution_analysis"
    output_root.mkdir(parents=True, exist_ok=True)
    analyzer = SolutionAnalyzer(root, mock=mock)
    schema = root / "schema/solution_analysis.schema.json"
    service = KnowledgeService(JsonArtifactRepository(root))
    files = service.list_analysis_contexts(query_id=query_id, case_id=case_id)
    parallel = load_parallel_execution_config(root)

    summary: dict[str, Any] = {
        "total": 0, "success": 0, "skipped": 0, "failed": 0,
        "schema_invalid": 0, "existing_skipped": 0,
        "similarity_missing": 0, "errors": [],
        "output_dir": str(output_root), "elapsed_seconds": 0.0,
        "execution_mode": "parallel" if parallel.enabled and parallel.max_workers > 1 else "serial",
        "parallel_workers": parallel.max_workers if parallel.enabled else 1,
    }
    started = time.perf_counter()
    if query_id and not files:
        summary["failed"] += 1
        summary["errors"].append({"query_id": query_id, "error": "ANALYSIS_CONTEXT_NOT_FOUND"})

    pending: list[Path] = []
    for source in files:
        qid, cid = source.parent.name, source.stem
        target = output_root / qid / f"{cid}.json"
        if target.exists() and not overwrite:
            summary["existing_skipped"] += 1
        else:
            pending.append(source)

    # M8.2 already completed deep similarity scoring. M8.3 only needs to analyze
    # the most relevant candidates, which prevents low-value candidates from
    # repeatedly sending large solution prompts. Filtering is per query.
    top_n, min_score = _solution_execution_config(root)
    grouped: dict[str, list[tuple[Path, float]]] = {}
    for source in pending:
        qid, cid = source.parent.name, source.stem
        grouped.setdefault(qid, []).append((source, _similarity_score(service, qid, cid)))
    selected: list[Path] = []
    filtered_count = 0
    for qid, items in grouped.items():
        ranked = sorted(items, key=lambda item: item[1], reverse=True)
        eligible = [item for item in ranked if item[1] >= min_score]
        if top_n > 0:
            eligible = eligible[:top_n]
        selected.extend(item[0] for item in eligible)
        filtered_count += len(items) - len(eligible)
    pending = selected
    summary["candidate_top_n"] = top_n
    summary["min_similarity_score"] = min_score
    summary["filtered_candidates"] = filtered_count
    summary["total"] = len(pending)
    summary["prompt_chars_total"] = 0
    summary["prompt_chars_max"] = 0
    summary["previous_payload_chars_total"] = 0

    def process(source: Path) -> dict[str, Any]:
        qid, cid = source.parent.name, source.stem
        try:
            artifacts = service.load_analysis_artifacts(qid, cid, context_path=source)
            similarity_missing = artifacts.similarity_analysis is None
            diagnostics = analyzer.input_diagnostics(
                artifacts.analysis_context, artifacts.similarity_analysis
            )
            result = analyzer.analyze(
                artifacts.analysis_context,
                similarity=artifacts.similarity_analysis,
                skip_ai=skip_ai,
            )
            errors = validate_json(result, schema)
            if errors:
                raise ValueError("; ".join(errors))
            service.save_solution_analysis(qid, cid, result)
            return {
                "status": result["analysis_status"],
                "file": str(source),
                "similarity_missing": similarity_missing,
                **diagnostics,
            }
        except Exception as exc:
            return {"status": "FAILED", "file": str(source), "error": str(exc), "similarity_missing": False}

    execution = parallel if not skip_ai else type(parallel)(enabled=False, max_workers=1)
    for item in ordered_map(pending, process, execution):
        compact_chars = int(item.get("compact_prompt_chars") or 0)
        previous_chars = int(item.get("previous_payload_chars") or 0)
        summary["prompt_chars_total"] += compact_chars
        summary["previous_payload_chars_total"] += previous_chars
        summary["prompt_chars_max"] = max(summary["prompt_chars_max"], compact_chars)
        if item.get("similarity_missing"):
            summary["similarity_missing"] += 1
        status = item["status"]
        if status == "SUCCESS":
            summary["success"] += 1
        elif status == "SKIPPED":
            summary["skipped"] += 1
        elif status == "AI_OUTPUT_INVALID":
            summary["schema_invalid"] += 1
        else:
            summary["failed"] += 1
            if item.get("error"):
                summary["errors"].append({"file": item["file"], "error": item["error"]})

    previous_total = summary.get("previous_payload_chars_total", 0)
    compact_total = summary.get("prompt_chars_total", 0)
    summary["prompt_reduction_percent"] = (
        round((1 - compact_total / previous_total) * 100, 1) if previous_total else 0.0
    )
    summary["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    write_json(root / "output/logs/m83_solution_summary.json", summary)
    return summary
