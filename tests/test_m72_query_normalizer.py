from pathlib import Path

from builder.query_normalizer import QueryNormalizer
from builder.validators import validate_json

ROOT = Path(__file__).resolve().parents[1]


def normalizer() -> QueryNormalizer:
    return QueryNormalizer(ROOT / "config/query_normalization.yaml")


def raw(mapped: dict, unmapped: dict | None = None) -> dict:
    return {"query_id": mapped.get("query_id", "Q1"), "mapped_fields": mapped, "unmapped_fields": unmapped or {}, "parser_version": "M7.1"}


def test_trim_and_mapping():
    result = normalizer().normalize(raw({"query_id": " Q1 ", "problem_description": "消息  处理   拥堵", "product": " PLC编程软件 ", "ipmt": "工业自动化 IPMT"}))
    assert result["original"]["product"] == " PLC编程软件 "
    assert result["normalized"]["query_id"] == "Q1"
    assert result["normalized"]["problem_description"] == "消息 处理 拥堵"
    assert result["normalized"]["product"] == "PLC"
    assert result["normalized"]["ipmt"] == "工业自动化"


def test_boolean_list_and_date(tmp_path):
    cfg = tmp_path / "normalization.yaml"
    cfg.write_text('''version: "1.0"\nnormalization:\n  null_values: [""]\n  boolean_mapping:\n    true: ["是", "true", "1"]\n    false: ["否", "false", "0"]\n  list_separators: [",", "，", ";", "；", "|"]\n  field_types:\n    flag: boolean\n    tags: list\n    happened_on: date\n''', encoding="utf-8")
    n = QueryNormalizer(cfg)
    result = n.normalize(raw({"query_id": "Q1", "problem_description": "x", "flag": " 是 ", "tags": "重启，卡顿;通信异常，重启", "happened_on": "2026年7月22日"}))
    assert result["normalized"]["flag"] is True
    assert result["normalized"]["tags"] == ["重启", "卡顿", "通信异常"]
    assert result["normalized"]["happened_on"] == "2026-07-22"


def test_failed_optional_field_is_partial(tmp_path):
    cfg = tmp_path / "normalization.yaml"
    cfg.write_text('''version: "1.0"\nnormalization:\n  null_values: [""]\n  boolean_mapping:\n    true: ["是"]\n    false: ["否"]\n  field_types:\n    query_id: string\n    problem_description: string\n    flag: boolean\n''', encoding="utf-8")
    result = QueryNormalizer(cfg).normalize(raw({"query_id": "Q1", "problem_description": "x", "flag": "不确定"}))
    assert result["normalize_status"] == "PARTIAL_SUCCESS"
    assert result["normalized"]["flag"] is None
    assert result["normalization_warnings"][0]["code"] == "BOOLEAN_NORMALIZATION_FAILED"


def test_extensions_and_schema():
    result = normalizer().normalize(raw({"query_id": "Q1", "problem_description": "x"}, {"临时字段": "保留"}))
    assert result["extensions"] == {"临时字段": "保留"}
    assert validate_json(result, ROOT / "schema/normalized_query.schema.json") == []


def test_deterministic_except_generated_at():
    n = normalizer()
    a = n.normalize(raw({"query_id": "Q1", "problem_description": " x "}))
    b = n.normalize(raw({"query_id": "Q1", "problem_description": " x "}))
    a["metadata"]["generated_at"] = ""
    b["metadata"]["generated_at"] = ""
    assert a == b
