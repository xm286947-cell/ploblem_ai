from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
import json

import yaml

from parser.common import write_json
from parser.evidence_parser import EvidenceParser


def load_yaml(path: str | Path) -> Dict[str, Any]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def run_m3(project_root: str | Path, case_id: str | None = None) -> dict:
    root = Path(project_root).resolve()
    app = load_yaml(root / "config/app.yaml")
    raw_evidence_dir = root / app["paths"]["raw_evidence_dir"]
    logs_dir = root / app["paths"]["logs_dir"]
    logs_dir.mkdir(parents=True, exist_ok=True)

    parser = EvidenceParser(root)
    source_files = sorted(raw_evidence_dir.glob("*.json"))
    if case_id:
        source_files = [raw_evidence_dir / f"{case_id}.json"]

    results: List[dict] = []
    failures: List[dict] = []
    for raw_path in source_files:
        if not raw_path.exists():
            failures.append({"case_id": case_id or raw_path.stem, "error": "RAW_EVIDENCE_NOT_FOUND"})
            continue
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        current_case_id = raw.get("case_id", raw_path.stem)
        report_path = raw.get("matched_report_path", "")
        if raw.get("parse_status") != "NOT_PARSED" or not report_path:
            results.append({"case_id": current_case_id, "status": "SKIPPED", "reason": raw.get("parse_status", "")})
            continue

        try:
            parsed = parser.parse(report_path)
            raw["page_count"] = parsed["page_count"]
            raw["total_characters"] = parsed["total_characters"]
            raw["tables"] = parsed.get("tables", [])
            raw["sections"] = parsed["sections"]
            raw["unclassified_blocks"] = parsed["unclassified_blocks"]
            raw["parse_warnings"] = sorted(set(raw.get("parse_warnings", []) + parsed["parse_warnings"]))
            raw["parse_status"] = "PARSED_WITH_WARNINGS" if raw["parse_warnings"] else "PARSED"
            raw["parsed_at"] = datetime.now(timezone.utc).isoformat()
            write_json(raw_path, raw)
            results.append({
                "case_id": current_case_id,
                "status": raw["parse_status"],
                "section_count": len(raw["sections"]),
                "warning_count": len(raw["parse_warnings"]),
            })
        except Exception as exc:
            raw["parse_status"] = "REPORT_PARSE_FAILED"
            raw.setdefault("parse_warnings", []).append(f"REPORT_PARSE_FAILED:{type(exc).__name__}:{exc}")
            raw["parsed_at"] = datetime.now(timezone.utc).isoformat()
            write_json(raw_path, raw)
            failures.append({"case_id": current_case_id, "error": str(exc), "error_type": type(exc).__name__})

    summary = {
        "stage": "M3",
        "total_raw_evidence": len(source_files),
        "processed_count": sum(1 for item in results if item["status"] in {"PARSED", "PARSED_WITH_WARNINGS"}),
        "skipped_count": sum(1 for item in results if item["status"] == "SKIPPED"),
        "failed_count": len(failures),
        "results": results,
        "failures": failures,
    }
    write_json(logs_dir / "m3_summary.json", summary)
    write_json(logs_dir / "pdf_parse_failures.json", {"items": failures})
    return summary
