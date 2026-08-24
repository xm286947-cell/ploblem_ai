from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List
import json

import yaml

from builder.evidence_fusion import EvidenceFusion
from builder.validators import validate_json
from parser.common import write_json


def load_yaml(path: str | Path) -> Dict[str, Any]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def run_m4(project_root: str | Path, case_id: str | None = None) -> dict:
    root = Path(project_root).resolve()
    app = load_yaml(root / "config/app.yaml")
    paths = app["paths"]

    raw_excel_dir = root / paths["raw_excel_dir"]
    raw_evidence_dir = root / paths["raw_evidence_dir"]
    standard_case_dir = root / paths["standard_case_dir"]
    logs_dir = root / paths["logs_dir"]
    schema_path = root / "schema/standard_case.schema.json"

    standard_case_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    source_files = sorted(raw_excel_dir.glob("*.json"))
    if case_id:
        source_files = [raw_excel_dir / f"{case_id}.json"]

    fusion = EvidenceFusion(app)
    results: List[dict] = []
    failures: List[dict] = []

    for excel_path in source_files:
        current_case_id = excel_path.stem
        if not excel_path.exists():
            failures.append({"case_id": current_case_id, "error": "RAW_EXCEL_NOT_FOUND"})
            continue

        evidence_path = raw_evidence_dir / excel_path.name
        try:
            raw_excel = json.loads(excel_path.read_text(encoding="utf-8"))
            if evidence_path.exists():
                raw_evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            else:
                raw_evidence = {
                    "case_id": raw_excel.get("case_id", current_case_id),
                    "report_filename": raw_excel.get("mapped_fields", {}).get("report_filename", ""),
                    "matched_report_path": "",
                    "parse_status": "REPORT_NOT_FOUND",
                    "sections": [],
                    "unclassified_blocks": [],
                    "parse_warnings": ["RAW_EVIDENCE_NOT_FOUND"],
                }

            standard_case = fusion.fuse(raw_excel, raw_evidence)
            validation_errors = validate_json(standard_case, schema_path)
            if validation_errors:
                failures.append({
                    "case_id": current_case_id,
                    "error": "SCHEMA_INVALID",
                    "details": validation_errors,
                })
                continue

            output_path = standard_case_dir / excel_path.name
            write_json(output_path, standard_case)
            results.append({
                "case_id": current_case_id,
                "status": standard_case["metadata"]["parse_status"],
                "evidence_status": standard_case["metadata"]["evidence_status"],
                "quality_flags": standard_case["knowledge"]["quality_flags"],
                "output": str(output_path),
            })
        except Exception as exc:
            failures.append({
                "case_id": current_case_id,
                "error": str(exc),
                "error_type": type(exc).__name__,
            })

    summary = {
        "stage": "M4",
        "total_cases": len(source_files),
        "success_count": len(results),
        "failed_count": len(failures),
        "results": results,
        "failures": failures,
    }
    write_json(logs_dir / "m4_summary.json", summary)
    write_json(logs_dir / "fusion_failures.json", {"items": failures})
    return summary
