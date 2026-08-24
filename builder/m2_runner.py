from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml

from parser.common import write_json
from parser.excel_parser import ExcelParser
from parser.report_matcher import ReportMatcher


def load_yaml(path: str | Path) -> Dict[str, Any]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def run_m2(
    project_root: str | Path,
    excel_path: str | Path | None = None,
    reports_dir: str | Path | None = None,
) -> dict:
    root = Path(project_root).resolve()
    app_config = load_yaml(root / "config" / "app.yaml")
    paths = app_config["paths"]
    runtime = app_config["runtime"]

    source_excel = Path(excel_path) if excel_path else root / paths["input_excel"]
    source_reports = Path(reports_dir) if reports_dir else root / paths["reports_dir"]
    raw_excel_dir = root / paths["raw_excel_dir"]
    raw_evidence_dir = root / paths["raw_evidence_dir"]
    logs_dir = root / paths["logs_dir"]

    raw_excel_dir.mkdir(parents=True, exist_ok=True)
    raw_evidence_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    excel_parser = ExcelParser(
        mapping_config=root / "config" / "field_mapping.yaml",
        case_id_prefix=runtime.get("case_id_prefix", "CASE"),
        case_id_width=int(runtime.get("case_id_width", 6)),
    )
    records, excel_summary = excel_parser.parse(source_excel, raw_excel_dir)

    matcher = ReportMatcher(root / "config" / "report_matching.yaml")
    evidence_results, match_summary = matcher.match_all(
        records=records,
        reports_dir=source_reports,
        output_dir=raw_evidence_dir,
    )

    unmatched = [
        item for item in evidence_results
        if item["match_type"] in {"NO_REPORT_NAME", "NOT_FOUND"}
    ]
    ambiguous = [
        item for item in evidence_results
        if item["match_type"] == "AMBIGUOUS"
    ]

    write_json(logs_dir / "excel_parse_summary.json", excel_summary.to_dict())
    write_json(logs_dir / "report_match_summary.json", match_summary)
    write_json(logs_dir / "unmatched_reports.json", {"items": unmatched})
    write_json(logs_dir / "duplicate_report_matches.json", {"items": ambiguous})

    result = {
        "stage": "M2",
        "excel": excel_summary.to_dict(),
        "report_matching": match_summary,
        "raw_excel_dir": str(raw_excel_dir),
        "raw_evidence_dir": str(raw_evidence_dir),
    }
    write_json(logs_dir / "m2_summary.json", result)
    return result
