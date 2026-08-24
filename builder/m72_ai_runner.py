from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from builder.query_ai_enricher import QueryAIEnricher
from builder.validators import validate_json
from parser.common import write_json


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def run_m72_ai(
    root: Path,
    query_id: str | None = None,
    overwrite: bool = False,
    mock: bool = False,
    skip_ai: bool = False,
) -> dict:
    source_dir = root / "knowledge/normalized_query"
    output_dir = root / "knowledge/enriched_query"
    output_dir.mkdir(parents=True, exist_ok=True)
    enricher = QueryAIEnricher(root, mock=mock)
    schema = root / "schema/enriched_query.schema.json"
    files = [source_dir / f"{query_id}.json"] if query_id else sorted(source_dir.glob("*.json"))
    summary = {
        "total": 0,
        "success": 0,
        "partial_success": 0,
        "failed": 0,
        "skipped": 0,
        "existing_skipped": 0,
        "schema_invalid": 0,
        "elapsed_seconds": 0.0,
        "errors": [],
        "output_dir": str(output_dir),
    }
    started = time.perf_counter()
    for source in files:
        if not source.exists():
            summary["errors"].append({"file": str(source), "error": "NORMALIZED_QUERY_NOT_FOUND"})
            summary["failed"] += 1
            continue
        target = output_dir / source.name
        if target.exists() and not overwrite:
            summary["existing_skipped"] += 1
            continue
        summary["total"] += 1
        try:
            normalized = _read(source)
            enriched = enricher.enrich(normalized, str(source.relative_to(root)), skip_ai=skip_ai)
            errors = validate_json(enriched, schema)
            if errors:
                raise ValueError("; ".join(errors))
            write_json(target, enriched)
            status = enriched["enrich_status"]
            if status == "SUCCESS":
                summary["success"] += 1
            elif status == "PARTIAL_SUCCESS":
                summary["partial_success"] += 1
            elif status == "SKIPPED":
                summary["skipped"] += 1
            elif status == "AI_OUTPUT_INVALID":
                summary["schema_invalid"] += 1
            else:
                summary["failed"] += 1
        except Exception as exc:
            summary["failed"] += 1
            summary["errors"].append({"file": str(source), "error": str(exc)})
    summary["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    write_json(root / "output/logs/m72_ai_summary.json", summary)
    return summary
