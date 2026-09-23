from pathlib import Path
import json

from builder.evidence_fusion import EvidenceFusion
from builder.m4_runner import run_m4
from builder.validators import validate_json
import yaml


ROOT = Path(__file__).resolve().parents[1]


def sample_raw_excel():
    return {
        "case_id": "CASE-900001",
        "source_excel": "input/cases.xlsx",
        "sheet_name": "Sheet1",
        "excel_row": 2,
        "raw_fields": {},
        "mapped_fields": {
            "itr_id": "ITR-900001",
            "assessment_year": "2026",
            "assessment_month": "7",
            "ipmt": "IPMT-A",
            "spdt": "SPDT-A",
            "responsible_department_level2": "部门A",
            "original_description": "初始问题描述",
            "trc_occurrence": "初始TRC发生",
            "trc_escape": "初始TRC流出",
            "mrc_occurrence": "",
            "mrc_escape": "",
            "report_filename": "sample.pdf",
            "cause_level1": "软件",
            "cause_level2": "设计",
        },
        "parse_status": "SUCCESS",
        "parse_warnings": [],
        "generated_at": "2026-01-01T00:00:00+00:00",
    }


def sample_raw_evidence():
    return {
        "case_id": "CASE-900001",
        "report_filename": "sample.pdf",
        "matched_report_path": "input/reports/sample.pdf",
        "candidate_paths": ["input/reports/sample.pdf"],
        "match_type": "EXACT_FILENAME",
        "file_hash": "abc",
        "file_size": 10,
        "parse_status": "PARSED",
        "sections": [
            {
                "section_type": "problem_description",
                "content": "报告中的准确问题描述",
                "page_numbers": [1],
                "confidence": 1.0,
            },
            {
                "section_type": "trc_occurrence",
                "content": "报告TRC发生分析",
                "page_numbers": [2],
                "confidence": 0.95,
            },
            {
                "section_type": "root_cause",
                "content": "最终根因",
                "page_numbers": [3],
                "confidence": 0.95,
            },
            {
                "section_type": "preventive_actions",
                "content": "预防措施",
                "page_numbers": [4],
                "confidence": 0.95,
            },
        ],
        "unclassified_blocks": [],
        "parse_warnings": [],
        "generated_at": "2026-01-01T00:00:00+00:00",
    }


def test_facts_only_fusion_schema_valid():
    app = yaml.safe_load((ROOT / "config/app.yaml").read_text(encoding="utf-8"))
    case = EvidenceFusion(app).fuse(sample_raw_excel(), sample_raw_evidence())
    assert case["problem"]["original_description"] == "初始问题描述"
    assert case["problem"]["report_description"] == "报告中的准确问题描述"
    assert case["problem"]["standard_description"] == ""
    assert case["analysis"]["trc"]["occurrence"]["original"] == "初始TRC发生"
    assert case["analysis"]["trc"]["occurrence"]["report"] == "报告TRC发生分析"
    assert case["analysis"]["trc"]["occurrence"]["standard"] == ""
    assert case["knowledge"]["ai_model"] == ""
    assert validate_json(case, ROOT / "schema/standard_case.schema.json") == []


def test_run_m4_writes_standard_case(tmp_path):
    raw_excel_dir = ROOT / "knowledge/raw_excel"
    raw_evidence_dir = ROOT / "knowledge/raw_evidence"
    standard_dir = ROOT / "knowledge/standard_case"
    excel_path = raw_excel_dir / "CASE-900001.json"
    evidence_path = raw_evidence_dir / "CASE-900001.json"
    output_path = standard_dir / "CASE-900001.json"

    excel_path.write_text(json.dumps(sample_raw_excel(), ensure_ascii=False), encoding="utf-8")
    evidence_path.write_text(json.dumps(sample_raw_evidence(), ensure_ascii=False), encoding="utf-8")
    try:
        result = run_m4(ROOT, case_id="CASE-900001")
        assert result["failed_count"] == 0
        assert output_path.exists()
    finally:
        excel_path.unlink(missing_ok=True)
        evidence_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)
