from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from builder.standard_query_builder import StandardQueryBuilder
from builder.validators import validate_json
from parser.common import write_json


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def run_m72_standard_builder(
    root: Path,
    query_id: str | None = None,
    overwrite: bool = False,
) -> dict:
    source_dir = root / "knowledge/enriched_query"
    output_dir = root / "knowledge/standard_query"
    output_dir.mkdir(parents=True, exist_ok=True)
    builder = StandardQueryBuilder(root)
    schema = root / "schema/standard_query.schema.json"
    files = [source_dir / f"{query_id}.json"] if query_id else sorted(source_dir.glob("*.json"))
    summary = {
        "total": 0,
        "success": 0,
        "partial_success": 0,
        "failed": 0,
        "existing_skipped": 0,
        "schema_invalid": 0,
        "elapsed_seconds": 0.0,
        "errors": [],
        "output_dir": str(output_dir),
    }
    started = time.perf_counter()
    for source in files:
        if not source.exists():
            summary["errors"].append({"file": str(source), "error": "ENRICHED_QUERY_NOT_FOUND"})
            summary["failed"] += 1
            continue
        target = output_dir / source.name
        if target.exists() and not overwrite:
            summary["existing_skipped"] += 1
            continue
        summary["total"] += 1
        try:
            enriched = _read(source)
            standard = builder.build(enriched, str(source.relative_to(root)))
            errors = validate_json(standard, schema)
            if errors:
                summary["schema_invalid"] += 1
                raise ValueError("; ".join(errors))
            write_json(target, standard)
            if standard["build_status"] == "SUCCESS":
                summary["success"] += 1
            else:
                summary["partial_success"] += 1
        except Exception as exc:
            summary["failed"] += 1
            summary["errors"].append({"file": str(source), "error": str(exc)})
    summary["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    write_json(root / "output/logs/m72_standard_builder_summary.json", summary)
    return summary
