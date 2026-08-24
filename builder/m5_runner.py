from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List
import json

import yaml

from builder.ai_enricher import AIEnricher
from builder.validators import validate_json
from parser.common import write_json


def load_yaml(path: str | Path) -> Dict[str, Any]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def run_m5(
    project_root: str | Path,
    case_id: str | None = None,
    mock: bool = False,
    overwrite: bool = False,
) -> dict:
    root = Path(project_root).resolve()
    app = load_yaml(root / "config/app.yaml")
    model = load_yaml(root / "config/model.yaml")
    paths = app["paths"]

    standard_dir = root / paths["standard_case_dir"]
    enriched_dir = root / paths["enriched_case_dir"]
    logs_dir = root / paths["logs_dir"]
    schema_path = root / "schema/standard_case.schema.json"

    enriched_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    source_files = sorted(standard_dir.glob("*.json"))
    if case_id:
        source_files = [standard_dir / f"{case_id}.json"]

    if not mock and not model["ai"].get("enabled", False):
        raise RuntimeError("AI未启用。请配置config/model.yaml中的ai.enabled=true，或使用--mock验证流程。")

    enricher = AIEnricher(root, model, mock=mock)
    results: List[dict] = []
    failures: List[dict] = []

    for source_path in source_files:
        current_case_id = source_path.stem
        if not source_path.exists():
            failures.append({"case_id": current_case_id, "error": "STANDARD_CASE_NOT_FOUND"})
            continue

        output_path = enriched_dir / source_path.name
        if output_path.exists() and not overwrite:
            results.append({
                "case_id": current_case_id,
                "status": "SKIPPED",
                "reason": "ENRICHED_CASE_EXISTS",
                "output": str(output_path),
            })
            continue

        try:
            standard_case = json.loads(source_path.read_text(encoding="utf-8"))
            enriched = enricher.enrich(standard_case)
            errors = validate_json(enriched, schema_path)
            if errors:
                failures.append({
                    "case_id": current_case_id,
                    "error": "SCHEMA_INVALID",
                    "details": errors,
                })
                continue

            write_json(output_path, enriched)
            results.append({
                "case_id": current_case_id,
                "status": "SUCCESS",
                "model": enriched["knowledge"]["ai_model"],
                "prompt_version": enriched["knowledge"]["prompt_version"],
                "classification_conflict": enriched["classification"]["classification_conflict"],
                "output": str(output_path),
            })
        except Exception as exc:
            failures.append({
                "case_id": current_case_id,
                "error": str(exc),
                "error_type": type(exc).__name__,
            })

    summary = {
        "stage": "M5",
        "mock": mock,
        "total_cases": len(source_files),
        "success_count": sum(1 for item in results if item["status"] == "SUCCESS"),
        "skipped_count": sum(1 for item in results if item["status"] == "SKIPPED"),
        "failed_count": len(failures),
        "results": results,
        "failures": failures,
    }
    write_json(logs_dir / "m5_summary.json", summary)
    write_json(logs_dir / "ai_enrich_failures.json", {"items": failures})
    return summary
