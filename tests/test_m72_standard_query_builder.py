from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from builder.standard_query_builder import StandardQueryBuilder
from builder.validators import validate_json

ROOT = Path(__file__).resolve().parents[1]


def _load(root: Path = ROOT, name: str = "QUERY001.json") -> dict:
    return json.loads((root / "knowledge/enriched_query" / name).read_text(encoding="utf-8"))


def test_builder_creates_schema_valid_standard_query() -> None:
    enriched = _load(ROOT)
    result = StandardQueryBuilder(ROOT).build(enriched, "knowledge/enriched_query/QUERY001.json")
    assert validate_json(result, ROOT / "schema/standard_query.schema.json") == []
    assert result["metadata"]["query_id"] == "QUERY001"


def test_effective_source_prefers_normalized_for_fact() -> None:
    result = StandardQueryBuilder(ROOT).build(_load(ROOT))
    assert result["organization"]["product"]["effective"] == "产品A"
    assert result["organization"]["product"]["effective_source"] == "NORMALIZED"


def test_analysis_field_prefers_ai() -> None:
    result = StandardQueryBuilder(ROOT).build(_load(ROOT))
    assert result["problem"]["problem_summary"]["effective_source"] == "INFERRED"
    assert "高负载" in result["problem"]["problem_summary"]["effective"]


def test_ai_corrected_classification_is_effective() -> None:
    result = StandardQueryBuilder(ROOT).build(_load(ROOT))
    field = result["classification"]["cause_level2"]
    assert field["effective"] == "资源与流量控制"
    assert field["effective_source"] == "INFERRED"
    assert field["normalized"] == "资源管理"


def test_classification_falls_back_to_normalized_when_ai_is_empty() -> None:
    enriched = _load(ROOT)
    enriched["inferred"]["classification"]["cause_level2"]["value"] = ""
    result = StandardQueryBuilder(ROOT).build(enriched)
    field = result["classification"]["cause_level2"]
    assert field["effective"] == "资源管理"
    assert field["effective_source"] == "NORMALIZED"


def test_builder_does_not_mutate_upstream() -> None:
    enriched = _load(ROOT)
    before = deepcopy(enriched)
    StandardQueryBuilder(ROOT).build(enriched)
    assert enriched == before


def test_ai_failure_builds_degraded_standard_query() -> None:
    enriched = _load(ROOT)
    enriched["enrich_status"] = "AI_ENRICH_FAILED"
    result = StandardQueryBuilder(ROOT).build(enriched)
    assert result["build_status"] == "PARTIAL_SUCCESS"
    assert "AI_ENRICHMENT_UNAVAILABLE" in result["quality_flags"]
    assert result["problem"]["problem_description"]["effective"]


def test_extensions_are_preserved() -> None:
    enriched = _load(ROOT)
    enriched["extensions"] = {"custom_field": "value"}
    result = StandardQueryBuilder(ROOT).build(enriched)
    assert result["extensions"] == {"custom_field": "value"}


def test_completeness_is_calculated() -> None:
    result = StandardQueryBuilder(ROOT).build(_load(ROOT))
    assert 0 <= result["completeness"]["input_completeness"] <= 1
    assert 0 <= result["completeness"]["analysis_completeness"] <= 1
