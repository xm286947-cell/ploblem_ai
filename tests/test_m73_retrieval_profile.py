from __future__ import annotations

import json
from pathlib import Path

from builder.retrieval_profile_builder import RetrievalProfileBuilder
from builder.validators import validate_json
from retriever.profile_adapter import profile_to_query_input

ROOT = Path(__file__).resolve().parents[1]


def _load_standard(root: Path) -> dict:
    return json.loads((root / "knowledge/standard_query/QUERY001.json").read_text(encoding="utf-8"))


def test_profile_builder_generates_valid_profile():
    profile = RetrievalProfileBuilder(ROOT).build(_load_standard(ROOT), "knowledge/standard_query/QUERY001.json")
    assert profile["query_id"] == "QUERY001"
    assert profile["high_weight"]
    assert not validate_json(profile, ROOT / "schema/retrieval_profile.schema.json")


def test_profile_keeps_filter_modes():
    profile = RetrievalProfileBuilder(ROOT).build(_load_standard(ROOT))
    modes = {item["field"]: item["filter_mode"] for item in profile["filter"]}
    assert modes["organization.product"] == "SOFT"
    assert modes["organization.ipmt"] == "PREFER"


def test_inferred_confidence_reduces_effective_weight():
    profile = RetrievalProfileBuilder(ROOT).build(_load_standard(ROOT))
    item = next(x for x in profile["high_weight"] if x["field"] == "problem.failure_objects")
    assert item["source"] == "INFERRED"
    assert 0 < item["effective_weight"] < item["base_weight"]


def test_empty_fields_generate_warnings():
    standard = _load_standard(ROOT)
    standard["problem"]["trigger_conditions"]["effective"] = []
    standard["problem"]["trigger_conditions"]["effective_source"] = "EMPTY"
    profile = RetrievalProfileBuilder(ROOT).build(standard)
    assert any(w["code"] == "PROFILE_FIELD_EMPTY" for w in profile["warnings"])


def test_profile_adapter_builds_legacy_query_input():
    profile = RetrievalProfileBuilder(ROOT).build(_load_standard(ROOT))
    query = profile_to_query_input(profile)
    assert query.text
    assert query.product == "产品A"
    assert query.ipmt == "IPMT-A"
    assert "消息" in query.text
    assert query.cause_description


def test_profile_unwraps_nested_evidence_objects_to_schema_scalars():
    standard = _load_standard(ROOT)
    standard["analysis"]["cause_description"]["effective"] = {
        "value": "封罩检测逻辑未覆盖抱闸异常场景",
        "evidence_type": "SUMMARIZED",
        "confidence": 0.85,
        "reason": "基于TRC归纳",
    }
    standard["solution"]["solution_mechanism"]["effective"] = {
        "value": "优化附加封罩输出逻辑",
        "evidence_type": "INFERRED",
        "confidence": 0.75,
        "reason": "基于措施描述归纳",
    }

    profile = RetrievalProfileBuilder(ROOT).build(standard)

    cause_entry = next(x for x in profile["medium_weight"] if x["field"] == "analysis.cause_description")
    action_entry = next(x for x in profile["low_weight"] if x["field"] == "solution.solution_mechanism")
    assert cause_entry["values"] == ["封罩检测逻辑未覆盖抱闸异常场景"]
    assert action_entry["values"] == ["优化附加封罩输出逻辑"]
    assert not validate_json(profile, ROOT / "schema/retrieval_profile.schema.json")
    assert all(not isinstance(v, (dict, list)) for entry in profile["high_weight"] + profile["medium_weight"] + profile["low_weight"] for v in entry["values"])
