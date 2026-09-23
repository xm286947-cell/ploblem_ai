from pathlib import Path
import json

from builder.m3_runner import run_m3
from builder.validators import load_json, validate_json

ROOT = Path(__file__).resolve().parents[1]


def test_m3_parse_sample_pdf():
    raw_dir = ROOT / "knowledge" / "raw_evidence"
    raw_dir.mkdir(parents=True, exist_ok=True)
    sample_pdf = ROOT / "tests" / "samples" / "reports" / "m3-sample.pdf"
    raw = {
        "case_id": "CASE-M3TEST",
        "report_filename": sample_pdf.name,
        "matched_report_path": str(sample_pdf.resolve()),
        "candidate_paths": [str(sample_pdf.resolve())],
        "match_type": "EXACT_FILENAME",
        "file_hash": "test",
        "file_size": sample_pdf.stat().st_size,
        "parse_status": "NOT_PARSED",
        "sections": [],
        "unclassified_blocks": [],
        "parse_warnings": [],
        "generated_at": "2026-07-22T00:00:00Z"
    }
    raw_path = raw_dir / "CASE-M3TEST.json"
    raw_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

    result = run_m3(ROOT, case_id="CASE-M3TEST")
    assert result["failed_count"] == 0
    parsed = load_json(raw_path)
    section_types = {item["section_type"] for item in parsed["sections"]}
    assert "problem_description" in section_types
    assert "trc_occurrence" in section_types
    assert "root_cause" in section_types
    errors = validate_json(parsed, ROOT / "schema" / "raw_evidence.schema.json")
    assert errors == []
