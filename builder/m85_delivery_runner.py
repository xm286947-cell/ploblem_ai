from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from parser.common import write_json
from presentation.delivery_service import DeliveryService


def _load_analysis(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"repeat_analysis根节点必须为对象: {path}")
    return value


def run_m85_delivery(root: Path, query_id: str | None = None, overwrite: bool = False) -> dict[str, Any]:
    source_root = root / "knowledge/repeat_analysis"
    output_root = root / "output/reports"
    service = DeliveryService(output_root)
    started = time.perf_counter()

    query_dirs = [source_root / query_id] if query_id else sorted(p for p in source_root.iterdir() if p.is_dir()) if source_root.exists() else []
    summary: dict[str, Any] = {
        "queries": 0,
        "success": 0,
        "skipped": 0,
        "failed": 0,
        "errors": [],
        "output_dir": str(output_root),
        "report_index_json": "",
        "report_index_markdown": "",
        "elapsed_seconds": 0.0,
    }
    deliveries: list[dict[str, Any]] = []

    if query_id and (not query_dirs or not query_dirs[0].exists()):
        summary["failed"] = 1
        summary["errors"].append({"query_id": query_id, "error": "REPEAT_ANALYSIS_NOT_FOUND"})
        query_dirs = []

    for qdir in query_dirs:
        qid = qdir.name
        source = qdir / "repeat_analysis.json"
        target = output_root / qid / "report.json"
        if not source.exists():
            summary["failed"] += 1
            summary["errors"].append({"query_id": qid, "error": "REPEAT_ANALYSIS_NOT_FOUND"})
            continue
        summary["queries"] += 1
        try:
            if target.exists() and not overwrite:
                existing = service.repository.load(qid)
                deliveries.append({
                    "query_id": qid,
                    "decision": (existing.get("repeat_decision") or {}).get("decision", "INSUFFICIENT_EVIDENCE"),
                    "confidence": (existing.get("repeat_decision") or {}).get("confidence", 0.0),
                    "analysis_status": (existing.get("summary") or {}).get("analysis_status", "SKIPPED"),
                    "report_json": str(target),
                    "report_markdown": str(output_root / qid / "report.md"),
                })
                summary["skipped"] += 1
                continue
            delivery = service.deliver(_load_analysis(source))
            deliveries.append(delivery)
            summary["success"] += 1
        except Exception as exc:
            summary["failed"] += 1
            summary["errors"].append({"query_id": qid, "error": str(exc)})

    index_paths = service.write_batch_index(deliveries)
    summary["report_index_json"] = str(index_paths["json"])
    summary["report_index_markdown"] = str(index_paths["markdown"])
    summary["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    write_json(root / "output/logs/m85_delivery_summary.json", summary)
    return summary
