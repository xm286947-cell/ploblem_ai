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
        ], start=1)
    ]
    repository.save_parse_result(document["version_id"], ParseResult("PDF", fragments))

    draft = MajorSemanticSourceAdapter(repository, ROOT).build_draft(case["case_id"])

    slots = draft["semantic_slots"]
    assert draft["source_fact_revision_id"] == fact["source_fact_revision_id"]
    assert slots["TRC_OCCURRENCE"]["values"] == ["复位保护时序设计不足"]
    assert slots["TRC_OCCURRENCE"]["status"] == "CONFLICT"
    assert slots["TRC_OCCURRENCE"]["evidence_refs"] == [
        {
            "source_type": "EXCEL",
            "source_fact_revision_id": fact["source_fact_revision_id"],
            "locator": "batch.xlsx#重大问题:7;field=trc_occurrence",
            "excerpt": "板级保护缺失",
        },
        {
            "source_type": "PDF",
            "fragment_id": repository.fragments(document["version_id"])[0]["fragment_id"],
            "locator": "page:1",
            "excerpt": "复位保护时序设计不足",
            "version_id": document["version_id"],
        },
    ]
    assert slots["TRC_ESCAPE"]["values"] == ["复位边界未纳入回归测试"]
    assert slots["MRC_OCCURRENCE"]["values"] == ["设计评审未识别异常窗口"]
    assert slots["MRC_ESCAPE"]["values"] == ["验收清单未要求该项验证"]
    assert slots["TECHNICAL_ACTION"]["values"] == ["增加硬件看门狗保护"]
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
