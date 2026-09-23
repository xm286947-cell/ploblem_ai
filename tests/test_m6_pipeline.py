from pathlib import Path
import json
import math

from builder.embedding_client import LocalHashEmbeddingClient
from builder.m6_runner import run_m6
from builder.retrieval_document_builder import RetrievalDocumentBuilder
from builder.validators import validate_json

ROOT = Path(__file__).resolve().parents[1]


def sample_enriched_case(case_id="CASE-920001"):
    return {
      "metadata":{"case_id":case_id,"itr_id":"ITR-920001","assessment_year":"2026","assessment_month":"7","report_filename":"a.pdf","source_excel":"input/cases.xlsx","source_report":"input/reports/a.pdf","builder_version":"1.0.0-m5","schema_version":"1.0","fusion_rule_version":"1.1","prompt_version":"1.0","model_version":"mock-model","source_file_version":"1.0","created_at":"2026-01-01","updated_at":"2026-01-01","generated_at":"2026-01-01","parse_status":"SUCCESS","evidence_status":"REPORT_PARSED"},
      "business_context":{"ipmt":"IPMT-A","spdt":"SPDT-A","responsible_department_level2":"部门A","organization_path":["IPMT-A","SPDT-A","部门A"],"product":"","domain":""},
      "problem":{"original_description":"初始问题","report_description":"报告问题","standard_description":"对象在条件下运行异常","problem_summary":"运行异常","phenomenon":[{"value":"运行异常","source_type":"AI","source_location":"AI_ENRICHER","confidence":0.75,"evidence_refs":[]}],"failure_object":[{"value":"目标对象","source_type":"AI","source_location":"AI_ENRICHER","confidence":0.75,"evidence_refs":[]}],"trigger_condition":[{"value":"指定条件","source_type":"AI","source_location":"AI_ENRICHER","confidence":0.75,"evidence_refs":[]}],"impact":[],"event_replay":[]},
      "analysis":{"trc":{"occurrence":{"original":"初始TRC","report":"报告TRC","standard":"异常处理逻辑缺陷","confidence":0.9,"evidence_refs":[]},"escape":{"original":"","report":"","standard":"边界测试不足","confidence":0.0,"evidence_refs":[]}},"mrc":{"occurrence":{"original":"","report":"","standard":"","confidence":0.0,"evidence_refs":[]},"escape":{"original":"","report":"","standard":"","confidence":0.0,"evidence_refs":[]}},"five_why":[],"root_cause":[],"failure_mechanism":[{"value":"异常状态未被正确处理","source_type":"AI","source_location":"AI_ENRICHER","confidence":0.75,"evidence_refs":[]}],"contributing_factors":[]},
      "classification":{"original":{"cause_level1":"软件","cause_level2":"设计"},"report_verified":{"cause_level1":"","cause_level2":"","evidence_refs":[]},"ai_inferred":{"cause_level1":"软件设计","cause_level2":"异常处理","reason":"相关","confidence":0.9},"classification_conflict":True,"conflict_description":"分类不同"},
      "solution":{"original_solution":[],"corrective_actions":[],"preventive_actions":[],"management_actions":[],"technical_actions":[],"reusable_actions":[{"value":"增加边界测试","source_type":"AI","source_location":"AI_ENRICHER","confidence":0.75,"evidence_refs":[]}],"action_status":[]},
      "knowledge":{"case_summary":"案例摘要","normalized_problem":"目标对象指定条件运行异常","phenomenon_tags":["运行异常"],"failure_object_tags":["目标对象"],"trigger_tags":["指定条件"],"failure_mechanism_tags":["异常处理缺陷"],"cause_tags":["设计缺陷"],"solution_tags":["边界测试"],"keywords":["运行异常","异常处理"],"retrieval_text":"IPMT-A SPDT-A 目标对象在指定条件下运行异常，异常处理逻辑缺陷，边界测试不足。","quality_flags":["CLASSIFICATION_CONFLICT"],"ai_model":"mock-model","prompt_version":"1.0","generated_at":"2026-01-01"}
    }


def test_retrieval_document_schema():
    doc = RetrievalDocumentBuilder().build(
        sample_enriched_case(),
        "knowledge/enriched_case/CASE-920001.json",
    )
    assert doc["classification"]["source"] == "AI"
    assert doc["organization"]["ipmt"] == "IPMT-A"
    assert "运行异常" in doc["tags"]
    assert doc["content_hash"]
    assert validate_json(doc, ROOT/"schema/retrieval_document.schema.json") == []


def test_local_hash_embedding_is_deterministic_and_normalized():
    client = LocalHashEmbeddingClient(dimensions=64)
    a = client.embed("相同文本")
    b = client.embed("相同文本")
    assert a.vector == b.vector
    assert len(a.vector) == 64
    norm = math.sqrt(sum(x*x for x in a.vector))
    assert abs(norm - 1.0) < 1e-8


def test_m6_runner_builds_assets():
    case_id = "CASE-920001"
    source = ROOT/"knowledge/enriched_case"/f"{case_id}.json"
    outputs = [
        ROOT/"knowledge/retrieval_docs"/f"{case_id}.json",
        ROOT/"knowledge/embeddings"/f"{case_id}.json",
    ]
    source.write_text(json.dumps(sample_enriched_case(case_id), ensure_ascii=False), encoding="utf-8")
    try:
        result = run_m6(ROOT, case_id=case_id, overwrite=True)
        assert result["success_count"] == 1
        assert result["failed_count"] == 0
        assert outputs[0].exists()
        assert outputs[1].exists()
        manifest = json.loads((ROOT/"knowledge/manifests/knowledge_manifest.json").read_text(encoding="utf-8"))
        assert manifest["document_count"] == 1
        assert (ROOT/"knowledge/index/case_index.jsonl").exists()
    finally:
        source.unlink(missing_ok=True)
        for path in outputs:
            path.unlink(missing_ok=True)
        for path in [
            ROOT/"knowledge/manifests/knowledge_manifest.json",
            ROOT/"knowledge/index/case_index.jsonl",
        ]:
            path.unlink(missing_ok=True)
