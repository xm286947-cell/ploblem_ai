from __future__ import annotations

import json
import time
from pathlib import Path

from builder.retrieval_profile_builder import RetrievalProfileBuilder
from builder.validators import validate_json
from parser.common import write_json


def run_m73_profile(root: Path, query_id: str | None = None, overwrite: bool = False) -> dict:
    source_dir = root / "knowledge/standard_query"
    output_dir = root / "knowledge/retrieval_profile"
    output_dir.mkdir(parents=True, exist_ok=True)
    files = [source_dir / f"{query_id}.json"] if query_id else sorted(source_dir.glob("*.json"))
    builder = RetrievalProfileBuilder(root)
    schema = root / "schema/retrieval_profile.schema.json"
    summary = {"stage": "M7.3.1-M7.3.2", "total": 0, "success": 0, "partial_success": 0, "failed": 0, "existing_skipped": 0, "schema_invalid": 0, "errors": [], "output_dir": str(output_dir), "elapsed_seconds": 0.0}
    started = time.perf_counter()
    for source in files:
        if not source.exists():
            summary["failed"] += 1
            summary["errors"].append({"file": str(source), "error": "STANDARD_QUERY_NOT_FOUND"})
            continue
        target = output_dir / source.name
        if target.exists() and not overwrite:
            summary["existing_skipped"] += 1
            continue
        summary["total"] += 1
        try:
            standard = json.loads(source.read_text(encoding="utf-8"))
            profile = builder.build(standard, str(source.relative_to(root)))
            errors = validate_json(profile, schema)
            if errors:
                summary["schema_invalid"] += 1
                raise ValueError("; ".join(errors))
            write_json(target, profile)
            summary["success" if profile["build_status"] == "SUCCESS" else "partial_success"] += 1
        except Exception as exc:
            summary["failed"] += 1
            summary["errors"].append({"file": str(source), "error": str(exc)})
    summary["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    write_json(root / "output/logs/m73_profile_summary.json", summary)
    return summary
