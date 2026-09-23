from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from builder.parallel_execution import load_parallel_execution_config, ordered_map
from builder.similarity_analyzer import SimilarityAnalyzer
from builder.validators import validate_json
from parser.common import write_json
from repositories import JsonArtifactRepository
from services import KnowledgeService


def run_m82_similarity(root: Path, query_id: str | None = None, case_id: str | None = None,
                       overwrite: bool = False, mock: bool = False, skip_ai: bool = False) -> dict[str, Any]:
    output_root = root / "knowledge/similarity_analysis"
    output_root.mkdir(parents=True, exist_ok=True)
    analyzer = SimilarityAnalyzer(root, mock=mock)
    schema = root / "schema/similarity_analysis.schema.json"
    service = KnowledgeService(JsonArtifactRepository(root))
    files = service.list_analysis_contexts(query_id=query_id, case_id=case_id)
    parallel = load_parallel_execution_config(root)

    summary: dict[str, Any] = {
        "total": 0, "success": 0, "skipped": 0, "failed": 0,
        "schema_invalid": 0, "existing_skipped": 0, "errors": [],
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
    summary["total"] = len(pending)

    def process(source: Path) -> dict[str, Any]:
        qid, cid = source.parent.name, source.stem
        try:
            artifacts = service.load_analysis_artifacts(qid, cid, context_path=source)
            result = analyzer.analyze(artifacts.analysis_context, skip_ai=skip_ai)
            errors = validate_json(result, schema)
            if errors:
                raise ValueError("; ".join(errors))
            service.save_similarity_analysis(qid, cid, result)
            return {"status": result["analysis_status"], "file": str(source)}
        except Exception as exc:
            return {"status": "FAILED", "file": str(source), "error": str(exc)}

    execution = parallel if not skip_ai else type(parallel)(enabled=False, max_workers=1)
    for item in ordered_map(pending, process, execution):
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

    summary["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    write_json(root / "output/logs/m82_similarity_summary.json", summary)
    return summary
