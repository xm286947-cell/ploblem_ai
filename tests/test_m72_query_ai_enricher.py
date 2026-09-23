from copy import deepcopy
from pathlib import Path

from builder.query_ai_enricher import QueryAIEnricher
from builder.validators import validate_json

ROOT = Path(__file__).resolve().parents[1]


def normalized() -> dict:
    return {
        "metadata": {"query_id": "Q1", "normalizer_version": "M7.2-N1", "normalization_config_version": "1.0"},
        "original": {"query_id": "Q1", "problem_description": " 高负载下消息拥堵并重启 "},
        "normalized": {"query_id": "Q1", "problem_description": "高负载下消息拥堵并重启"},
        "extensions": {"临时字段": "保留"},
    }


def test_mock_ai_success_and_readonly():
    source = normalized()
    before = deepcopy(source)
    result = QueryAIEnricher(ROOT, mock=True).enrich(source, "knowledge/normalized_query/Q1.json")
    assert result["enrich_status"] == "SUCCESS"
    assert result["original"] == before["original"]
    assert result["normalized"] == before["normalized"]
    assert source == before
    assert result["inferred"]["failure_objects"]
    assert validate_json(result, ROOT / "schema/enriched_query.schema.json") == []


def test_skip_ai_generates_valid_degraded_result():
    result = QueryAIEnricher(ROOT, mock=True).enrich(normalized(), skip_ai=True)
    assert result["enrich_status"] == "SKIPPED"
    assert result["inferred"]["overall_confidence"]["value"] == 0.0
    assert validate_json(result, ROOT / "schema/enriched_query.schema.json") == []


def test_invalid_ai_output_degrades(tmp_path):
    class InvalidClient:
        def complete(self, messages):
            class R:
                content = '{"problem_summary": "bad"}'
                model = "invalid-model"
            return R()

    result = QueryAIEnricher(ROOT, client=InvalidClient()).enrich(normalized())
    assert result["enrich_status"] == "AI_OUTPUT_INVALID"
    assert result["inferred"]["problem_summary"]["evidence_type"] == "UNKNOWN"
    assert validate_json(result, ROOT / "schema/enriched_query.schema.json") == []


def test_client_error_degrades():
    class BrokenClient:
        def complete(self, messages):
            from builder.ai_client import AIClientError
            raise AIClientError("timeout")

    result = QueryAIEnricher(ROOT, client=BrokenClient()).enrich(normalized())
    assert result["enrich_status"] == "AI_ENRICH_FAILED"
    assert result["ai_warnings"][0]["code"] == "AI_ENRICH_FAILED"


def test_mixed_evidence_shapes_are_migrated_before_validation():
    class MixedShapeClient:
        def complete(self, messages):
            import json
            from tests.samples_shape import mixed_shape_response
            class R:
                content = json.dumps(mixed_shape_response(), ensure_ascii=False)
                model = "mixed-shape-model"
            return R()

    result = QueryAIEnricher(ROOT, client=MixedShapeClient()).enrich(normalized())
    assert result["enrich_status"] == "SUCCESS"
    assert result["inferred"]["keywords"][0]["value"] == "接线错误"
    assert result["inferred"]["operating_context"][0]["value"] == "客户现场"
    assert result["inferred"]["overall_confidence"]["value"] == 0.8
    assert validate_json(result, ROOT / "schema/enriched_query.schema.json") == []
