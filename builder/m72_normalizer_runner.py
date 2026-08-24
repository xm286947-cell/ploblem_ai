from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from builder.query_normalizer import QueryNormalizer
from builder.validators import validate_json
from parser.common import write_json


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def run_m72_normalizer(root: Path, query_id: str | None = None, overwrite: bool = False) -> dict:
    source_dir = root / "knowledge/raw_query"
    output_dir = root / "knowledge/normalized_query"
    output_dir.mkdir(parents=True, exist_ok=True)
    normalizer = QueryNormalizer(root / "config/query_normalization.yaml")
    schema = root / "schema/normalized_query.schema.json"
    files = [source_dir / f"{query_id}.json"] if query_id else sorted(source_dir.glob("*.json"))
    summary = {"total": 0, "success": 0, "partial_success": 0, "failed": 0, "skipped": 0, "errors": [], "output_dir": str(output_dir)}
    for source in files:
        if not source.exists():
            summary["errors"].append({"file": str(source), "error": "RAW_QUERY_NOT_FOUND"})
            summary["failed"] += 1
            continue
        target = output_dir / source.name
        if target.exists() and not overwrite:
            summary["skipped"] += 1
            continue
        summary["total"] += 1
        try:
            raw_query = _read(source)
            normalized = normalizer.normalize(raw_query, str(source.relative_to(root)))
            errors = validate_json(normalized, schema)
            if errors:
                raise ValueError("; ".join(errors))
            write_json(target, normalized)
            status = normalized["normalize_status"]
            if status == "SUCCESS": summary["success"] += 1
            elif status == "PARTIAL_SUCCESS": summary["partial_success"] += 1
            else: summary["failed"] += 1
        except Exception as exc:
            summary["failed"] += 1
            summary["errors"].append({"file": str(source), "error": str(exc)})
    write_json(root / "output/logs/m72_normalizer_summary.json", summary)
    return summary
