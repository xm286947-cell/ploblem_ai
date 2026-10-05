from __future__ import annotations

from pathlib import Path

from quality_knowledge.major_cases.document_parser import ParseResult, ParsedFragment
from quality_knowledge.major_cases.repository import MajorKnowledgeRepository
from quality_knowledge.major_cases.semantic_source_adapter import MajorSemanticSourceAdapter


ROOT = Path(__file__).resolve().parents[1]


def _repository(tmp_path: Path) -> MajorKnowledgeRepository:
    return MajorKnowledgeRepository(tmp_path / "major.db", tmp_path / "attachments")


def test_bridge_fuses_excel_causes_and_document_action_sections_with_formal_refs(tmp_path: Path):
    repository = _repository(tmp_path)
    case = repository.create_case("semantic bridge", "MAJOR")
    fact = repository.add_source_fact_revision(
        case["case_id"],
        source_type="EXCEL",
        source_ref="batch.xlsx#重大问题:7",
        raw={"TRC发生": "板级保护缺失"},
        normalized={
            "itr_id": "ITR20261001",
            "original_description": "设备异常复位",
            "trc_occurrence": "板级保护缺失",
            "trc_escape": "测试未覆盖复位边界",
            "mrc_occurrence": "评审遗漏风险",
            "mrc_escape": "验收检查表缺项",
        },
        actor="test",
    )
    source = tmp_path / "report.pdf"
    source.write_bytes(b"source document placeholder")
    document = repository.ingest_file(case["case_id"], source)
    fragments = [
        ParsedFragment(index, "", "PAGE", f"page:{index}", "TEXT", f"{heading}\n{content}")
        for index, (heading, content) in enumerate([
            ("TRC发生", "复位保护时序设计不足"),
            ("TRC流出", "复位边界未纳入回归测试"),
            ("MRC发生", "设计评审未识别异常窗口"),
            ("MRC流出", "验收清单未要求该项验证"),
            ("纠正措施", "修复当前版本的保护时序"),
            ("预防措施", "增加复位窗口自动化测试"),
            ("管理措施", "更新评审检查表并培训团队"),
            ("技术措施", "增加硬件看门狗保护"),
            ("技术措施", "增加掉电恢复状态校验"),
        ], start=1)
    ]
    repository.save_parse_result(document["version_id"], ParseResult("PDF", fragments))

    draft = MajorSemanticSourceAdapter(repository, ROOT).build_draft(case["case_id"])

    slots = draft["semantic_slots"]
    assert draft["source_fact_revision_id"] == fact["source_fact_revision_id"]
    assert slots["TRC_OCCURRENCE"]["values"] == ["板级保护缺失", "复位保护时序设计不足"]
    assert slots["TRC_OCCURRENCE"]["status"] == "MULTI_SOURCE"
    assert slots["TRC_OCCURRENCE"]["review_status"] == "REVIEW_REQUIRED"
    assert slots["TRC_OCCURRENCE"]["items"] == [
        {
            "value": "板级保护缺失",
            "source_type": "EXCEL",
            "evidence_refs": [{
                "source_type": "EXCEL",
                "source_fact_revision_id": fact["source_fact_revision_id"],
                "locator": "batch.xlsx#重大问题:7;field=trc_occurrence",
                "excerpt": "板级保护缺失",
            }],
        },
        {
            "value": "复位保护时序设计不足",
            "source_type": "PDF",
            "evidence_refs": [{
                "source_type": "PDF",
                "fragment_id": repository.fragments(document["version_id"])[0]["fragment_id"],
                "locator": "page:1",
                "excerpt": "复位保护时序设计不足",
                "version_id": document["version_id"],
            }],
        },
    ]
    assert slots["TRC_ESCAPE"]["values"] == ["测试未覆盖复位边界", "复位边界未纳入回归测试"]
    assert slots["MRC_OCCURRENCE"]["values"] == ["评审遗漏风险", "设计评审未识别异常窗口"]
    assert slots["MRC_ESCAPE"]["values"] == ["验收检查表缺项", "验收清单未要求该项验证"]
    assert slots["TECHNICAL_ACTION"]["values"] == ["增加硬件看门狗保护", "增加掉电恢复状态校验"]
    technical_items = slots["TECHNICAL_ACTION"]["items"]
    assert [item["value"] for item in technical_items] == ["增加硬件看门狗保护", "增加掉电恢复状态校验"]
    assert [item["evidence_refs"][0]["fragment_id"] for item in technical_items] == [
        repository.fragments(document["version_id"])[7]["fragment_id"],
        repository.fragments(document["version_id"])[8]["fragment_id"],
    ]
    assert [item["evidence_refs"][0]["excerpt"] for item in technical_items] == [
        "增加硬件看门狗保护", "增加掉电恢复状态校验",
    ]
    assert slots["MANAGEMENT_ACTION"]["values"] == ["更新评审检查表并培训团队"]
    assert slots["CORRECTIVE_ACTION"]["values"] == ["修复当前版本的保护时序"]
    assert slots["PREVENTIVE_ACTION"]["values"] == ["增加复位窗口自动化测试"]
    assert all(slots[key]["evidence_refs"] for key in slots)


def test_bridge_keeps_missing_semantics_fail_safe_and_does_not_write_entries(tmp_path: Path):
    repository = _repository(tmp_path)
    case = repository.create_case("missing semantic", "MAJOR")
    repository.add_source_fact_revision(
        case["case_id"], source_type="EXCEL", source_ref="batch.xlsx#重大问题:8",
        raw={}, normalized={"trc_occurrence": "已知发生原因"}, actor="test",
    )

    draft = MajorSemanticSourceAdapter(repository, ROOT).build_draft(case["case_id"])

    assert draft["semantic_slots"]["TRC_OCCURRENCE"]["status"] == "AVAILABLE"
    assert draft["semantic_slots"]["MRC_ESCAPE"]["status"] == "MISSING"
    assert draft["semantic_slots"]["MRC_ESCAPE"]["values"] == []
    assert repository.entries(case["case_id"]) == []


def test_undetermined_opposite_wording_requires_review_without_claiming_conflict(tmp_path: Path):
    repository = _repository(tmp_path)
    case = repository.create_case("undetermined source wording", "MAJOR")
    repository.add_source_fact_revision(
        case["case_id"], source_type="EXCEL", source_ref="batch.xlsx#重大问题:9",
        raw={}, normalized={"trc_occurrence": "未启用掉电保护"}, actor="test",
    )
    source = tmp_path / "opposite.pdf"
    source.write_bytes(b"source document placeholder")
    document = repository.ingest_file(case["case_id"], source)
    repository.save_parse_result(document["version_id"], ParseResult("PDF", [
        ParsedFragment(1, "", "PAGE", "page:1", "TEXT", "TRC发生\n已启用掉电保护"),
    ]))

    slot = MajorSemanticSourceAdapter(repository, ROOT).build_draft(case["case_id"])["semantic_slots"]["TRC_OCCURRENCE"]

    assert slot["status"] == "MULTI_SOURCE"
    assert slot["review_status"] == "REVIEW_REQUIRED"


def test_formal_source_relation_conflict_is_exposed_as_conflict(tmp_path: Path):
    repository = _repository(tmp_path)
    case = repository.create_case("formal relation conflict", "MAJOR")
    fact = repository.add_source_fact_revision(
        case["case_id"], source_type="EXCEL", source_ref="batch.xlsx#重大问题:10",
        raw={}, normalized={"trc_occurrence": "Excel 原因"}, actor="test",
    )
    relation = repository.add_source_link(
        case["case_id"], None,
        {"record_id": fact["source_fact_revision_id"], "source_type": "MAJOR_EXCEL_SOURCE_FACT"},
        standard_itr="", role="CURRENT_EVENT", status="CONFLICT",
    )

    slot = MajorSemanticSourceAdapter(repository, ROOT).build_draft(case["case_id"])["semantic_slots"]["TRC_OCCURRENCE"]

    assert slot["status"] == "CONFLICT"
    assert slot["review_status"] == "REVIEW_REQUIRED"
    assert relation["source_link_id"] in slot["source_relation_conflicts"]


def test_later_non_excel_source_fact_does_not_replace_latest_excel_fact(tmp_path: Path):
    repository = _repository(tmp_path)
    case = repository.create_case("typed source selection", "MAJOR")
    excel_fact = repository.add_source_fact_revision(
        case["case_id"], source_type="EXCEL", source_ref="batch.xlsx#重大问题:11",
        raw={}, normalized={"trc_occurrence": "Excel 最新原因"}, actor="test",
    )
    repository.add_source_fact_revision(
        case["case_id"], source_type="DOCUMENT", source_ref="report.pdf#page:2",
        raw={}, normalized={"trc_occurrence": "Document 后写入的原因"}, actor="test",
    )

    draft = MajorSemanticSourceAdapter(repository, ROOT).build_draft(case["case_id"])

    assert draft["source_fact_revision_id"] == excel_fact["source_fact_revision_id"]
    assert draft["semantic_slots"]["TRC_OCCURRENCE"]["values"] == ["Excel 最新原因"]


def test_bridge_uses_latest_document_version_per_document(tmp_path: Path):
    repository = _repository(tmp_path)
    case = repository.create_case("current document version", "MAJOR")
    old_path = tmp_path / "old.pdf"
    old_path.write_bytes(b"old version bytes")
    old_document = repository.ingest_file(case["case_id"], old_path, logical_name="report")
    repository.save_parse_result(old_document["version_id"], ParseResult("PDF", [
        ParsedFragment(1, "", "PAGE", "page:1", "TEXT", "TRC发生\n旧版本原因"),
    ]))
    new_path = tmp_path / "new.pdf"
    new_path.write_bytes(b"new version bytes")
    new_document = repository.ingest_file(
        case["case_id"], new_path, document_id=old_document["document_id"], logical_name="report"
    )
    repository.save_parse_result(new_document["version_id"], ParseResult("PDF", [
        ParsedFragment(1, "", "PAGE", "page:1", "TEXT", "TRC发生\n新版本原因"),
    ]))

    draft = MajorSemanticSourceAdapter(repository, ROOT).build_draft(case["case_id"])

    assert draft["document_version_ids"] == [new_document["version_id"]]
    assert draft["semantic_slots"]["TRC_OCCURRENCE"]["values"] == ["新版本原因"]
