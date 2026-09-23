from pathlib import Path
import copy
import json
import yaml

from builder.ai_enricher import AIEnricher
from builder.m5_runner import run_m5
from builder.validators import validate_json

ROOT = Path(__file__).resolve().parents[1]


def sample_case():
    return {
      "metadata":{"case_id":"CASE-910001","itr_id":"ITR-1","assessment_year":"2026","assessment_month":"7","report_filename":"a.pdf","source_excel":"input/cases.xlsx","source_report":"input/reports/a.pdf","builder_version":"1.0.0-m4","schema_version":"1.0","fusion_rule_version":"1.1","prompt_version":"","model_version":"","source_file_version":"1.0","created_at":"2026-01-01","updated_at":"2026-01-01","generated_at":"2026-01-01","parse_status":"SUCCESS","evidence_status":"REPORT_PARSED"},
      "business_context":{"ipmt":"IPMT-A","spdt":"SPDT-A","responsible_department_level2":"部门A","organization_path":["IPMT-A","SPDT-A","部门A"],"product":"","domain":""},
      "problem":{"original_description":"初始问题","report_description":"报告问题","standard_description":"","problem_summary":"","phenomenon":[],"failure_object":[],"trigger_condition":[],"impact":[],"event_replay":[]},
      "analysis":{"trc":{"occurrence":{"original":"初始TRC","report":"报告TRC","standard":"","confidence":0.9,"evidence_refs":[]},"escape":{"original":"","report":"","standard":"","confidence":0.0,"evidence_refs":[]}},"mrc":{"occurrence":{"original":"","report":"","standard":"","confidence":0.0,"evidence_refs":[]},"escape":{"original":"","report":"","standard":"","confidence":0.0,"evidence_refs":[]}},"five_why":[],"root_cause":[],"failure_mechanism":[],"contributing_factors":[]},
      "classification":{"original":{"cause_level1":"原分类","cause_level2":"原二级"},"report_verified":{"cause_level1":"","cause_level2":"","evidence_refs":[]},"ai_inferred":{"cause_level1":"","cause_level2":"","reason":"","confidence":0.0},"classification_conflict":False,"conflict_description":""},
      "solution":{"original_solution":[],"corrective_actions":[],"preventive_actions":[],"management_actions":[],"technical_actions":[],"reusable_actions":[],"action_status":[]},
      "knowledge":{"case_summary":"","normalized_problem":"","phenomenon_tags":[],"failure_object_tags":[],"trigger_tags":[],"failure_mechanism_tags":[],"cause_tags":[],"solution_tags":[],"keywords":[],"retrieval_text":"","quality_flags":[],"ai_model":"","prompt_version":"","generated_at":""}
    }


def test_mock_enricher_keeps_fact_layer():
    original = sample_case()
    snapshot = copy.deepcopy(original)
    model = yaml.safe_load((ROOT/"config/model.yaml").read_text(encoding="utf-8"))
    enriched = AIEnricher(ROOT, model, mock=True).enrich(original)
    assert enriched["problem"]["original_description"] == snapshot["problem"]["original_description"]
    assert enriched["problem"]["report_description"] == snapshot["problem"]["report_description"]
    assert enriched["analysis"]["trc"]["occurrence"]["original"] == "初始TRC"
    assert enriched["analysis"]["trc"]["occurrence"]["report"] == "报告TRC"
    assert enriched["problem"]["standard_description"]
    assert enriched["knowledge"]["retrieval_text"]
    assert enriched["knowledge"]["ai_model"] == "mock-model"
    assert validate_json(enriched, ROOT/"schema/standard_case.schema.json") == []


def test_m5_runner_mock(tmp_path):
    standard_dir = ROOT/"knowledge/standard_case"
    enriched_dir = ROOT/"knowledge/enriched_case"
    source = standard_dir/"CASE-910001.json"
    target = enriched_dir/"CASE-910001.json"
    source.write_text(json.dumps(sample_case(),ensure_ascii=False),encoding="utf-8")
    try:
        result = run_m5(ROOT, case_id="CASE-910001", mock=True, overwrite=True)
        assert result["success_count"] == 1
        assert result["failed_count"] == 0
        assert target.exists()
    finally:
        source.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
