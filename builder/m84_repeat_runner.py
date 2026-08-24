from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from builder.repeat_decision import RepeatDecisionEngine
from parser.common import write_json
from presentation.contract.analysis_result import AnalysisResult
from presentation.construction.report_builder import ReportBuilder
from presentation.repository.file_report_repository import FileReportRepository
from repositories import JsonArtifactRepository
from services import KnowledgeService


def run_m84_decision(root: Path, query_id: str | None = None, overwrite: bool = False,
                     mock: bool = False, skip_ai: bool = False) -> dict[str, Any]:
    context_root = root / "knowledge/analysis_context"
    output_root = root / "knowledge/repeat_analysis"
    output_root.mkdir(parents=True, exist_ok=True)
    engine = RepeatDecisionEngine(root, mock=mock)
    report_builder = ReportBuilder()
    report_repository = FileReportRepository(output_root)
    service = KnowledgeService(JsonArtifactRepository(root))

    query_dirs = [context_root / query_id] if query_id else sorted(p for p in context_root.iterdir() if p.is_dir()) if context_root.exists() else []
    summary: dict[str, Any] = {
        "queries": 0, "success": 0, "partial_success": 0, "skipped": 0, "failed": 0,
        "candidates": 0, "similarity_missing": 0, "solution_missing": 0,
        "existing_skipped": 0, "errors": [], "output_dir": str(output_root), "elapsed_seconds": 0.0,
    }
    started = time.perf_counter()
    if query_id and (not query_dirs or not query_dirs[0].exists()):
        summary["failed"] += 1
        summary["errors"].append({"query_id": query_id, "error": "ANALYSIS_CONTEXT_NOT_FOUND"})
    for qdir in query_dirs:
        qid = qdir.name
        target = output_root / qid / "repeat_analysis.json"
        if target.exists() and not overwrite:
            summary["existing_skipped"] += 1
            continue
        summary["queries"] += 1
        try:
            items = []
            for context_file in service.list_analysis_contexts(query_id=qid):
                cid = context_file.stem
                artifacts = service.load_analysis_artifacts(qid, cid, context_path=context_file)
                if artifacts.similarity_analysis is None:
                    summary["similarity_missing"] += 1
                if artifacts.solution_analysis is None:
                    summary["solution_missing"] += 1
                # Department is only a retrieval preference. It must not take part
                # in repeat decision or remove a candidate at M8.4.
                items.append((
                    artifacts.analysis_context,
                    artifacts.similarity_analysis,
                    artifacts.solution_analysis,
                ))
            summary["candidates"] += len(items)
            result = engine.build_analysis(qid, items, skip_ai=skip_ai)
            service.save_repeat_analysis(qid, result)
            analysis_result = AnalysisResult.from_mapping(result)
            report = report_builder.build(analysis_result)
            report_repository.save(qid, report)
            status = result["analysis_status"]
            if status == "SUCCESS": summary["success"] += 1
            elif status == "PARTIAL_SUCCESS": summary["partial_success"] += 1
            elif status == "SKIPPED": summary["skipped"] += 1
            else: summary["failed"] += 1
        except Exception as exc:
            summary["failed"] += 1
            summary["errors"].append({"query_id": qid, "error": str(exc)})
    summary["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    write_json(root / "output/logs/m84_repeat_decision_summary.json", summary)
    return summary
