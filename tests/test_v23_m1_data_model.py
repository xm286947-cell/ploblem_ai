import json

from compatibility import adapt_legacy_m7_query
from contracts import SchemaRegistry, generate_prompt_schema, generate_runtime_schema
from models import DecisionType, EvidenceField, QueryContext, RepeatDecision
from parsing import AIOutputParser


def test_evidence_field_and_query_context_use_native_fact_fields():
    query = QueryContext(
        query_id="Q-001",
        product="PLC",
        problem_description="CAN receive queue congestion",
        feature=EvidenceField(value="CAN receive", confidence=0.9, evidence_type="INFERRED", reason="text evidence"),
    )
    assert query.product == "PLC"
    assert query.feature.value == "CAN receive"
    assert query.dto_version == "1.0.0"


def test_runtime_and_prompt_schema_derive_from_same_dto():
    runtime = generate_runtime_schema(QueryContext)
    prompt = generate_prompt_schema(QueryContext)
    assert "properties" in runtime
    assert "properties" in prompt
    assert "query_id" in runtime["properties"]
    assert "query_id" in prompt["properties"]
    assert "title" not in prompt


def test_schema_registry():
    registry = SchemaRegistry()
    registry.register("query_context@1.0.0", QueryContext)
    assert registry.get("query_context@1.0.0") is QueryContext
    assert "query_id" in registry.runtime_schema("query_context@1.0.0")["properties"]


def test_parser_reports_precise_validation_path():
    parser = AIOutputParser(RepeatDecision)
    result = parser.parse(json.dumps({
        "query_id": "Q-001",
        "decision": "INVALID",
        "confidence": 2,
    }))
    assert not result.success
    paths = {issue.path for issue in result.validation_errors}
    assert "decision" in paths
    assert "confidence" in paths


def test_parser_accepts_markdown_fenced_json():
    parser = AIOutputParser(RepeatDecision)
    result = parser.parse("""```json
    {"query_id":"Q-001","decision":"REPEAT","confidence":0.91}
    ```""")
    assert result.success
    assert result.value.decision == DecisionType.REPEAT.value


def test_legacy_m7_string_keywords_are_upgraded_to_evidence_objects():
    legacy = {
        "metadata": {"query_id": "Q-001"},
        "original": {"product": "PLC", "problem_description": "receive congestion"},
        "inferred": {
            "keywords": ["CAN", "congestion"],
            "tags": ["communication"],
            "phenomena": ["restart"],
            "overall_confidence": 0.8,
        },
    }
    parser = AIOutputParser(QueryContext, adapter=adapt_legacy_m7_query)
    result = parser.parse(legacy)
    assert result.success
    assert result.value.keywords[0].value == "CAN"
    assert result.value.keywords[0].confidence == 0.5
