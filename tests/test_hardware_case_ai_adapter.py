from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

from repositories.hardware_case_repository import HardwareCaseRepository
from services.hardware_case_ai_adapter import HardwareCaseAIAdapter, derive_identity
from services.hardware_case_backend import HardwareCaseBackendService
from services.hardware_case_word import parse_docx


DOC_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
 xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
 <w:body>
  <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>问题描述</w:t></w:r></w:p>
  <w:p><w:r><w:t>设备上电后出现复位。</w:t></w:r></w:p>
  <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>原因分析</w:t></w:r></w:p>
  <w:p><w:r><w:t>输入浪涌触发保护。</w:t></w:r>
    <w:r><w:drawing><a:blip r:embed="rId5"/></w:drawing></w:r>
  </w:p>
  <w:tbl>
    <w:tr><w:tc><w:p><w:r><w:t>测试项</w:t></w:r></w:p></w:tc>
    <w:tc><w:p><w:r><w:t>结果</w:t></w:r></w:p></w:tc></w:tr>
    <w:tr><w:tc><w:p><w:r><w:t>浪涌</w:t></w:r></w:p></w:tc>
    <w:tc><w:p><w:r><w:t>复现</w:t></w:r></w:p></w:tc></w:tr>
  </w:tbl>
  <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>解决方案</w:t></w:r></w:p>
  <w:p><w:r><w:t>增加输入保护器件。</w:t></w:r></w:p>
 </w:body>
</w:document>
"""

RELS_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId5" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image1.png"/>
</Relationships>
"""


def _docx(path: Path, name="A1234-电源上电复位.docx") -> Path:
    target = path / name
    with ZipFile(target, "w") as archive:
        archive.writestr("word/document.xml", DOC_XML)
        archive.writestr("word/_rels/document.xml.rels", RELS_XML)
        archive.writestr("word/media/image1.png", b"synthetic-image")
    return target


def _backend(tmp_path: Path) -> HardwareCaseBackendService:
    return HardwareCaseBackendService(
        HardwareCaseRepository(tmp_path / "hardware_case.sqlite3")
    )


def _trees(service: HardwareCaseBackendService):
    service.save_tree_node(
        {
            "node_id": "CF-POWER",
            "tree_type": "CIRCUIT_FEATURE",
            "name": "输入保护",
            "path": ["电源", "输入保护"],
            "source_ref": "synthetic:circuit.xlsx",
            "active": True,
        }
    )
    service.save_tree_node(
        {
            "node_id": "MD-PROTECT",
            "tree_type": "MATERIAL_DEVICE",
            "name": "保护器件",
            "path": ["采购件", "保护器件"],
            "source_ref": "synthetic:material.xlsx",
            "active": True,
        }
    )


def _structurer(document):
    by_text = {
        block.get("text"): block["block_id"]
        for block in document["blocks"]
        if block.get("text")
    }
    return {
        "title": "电源上电复位",
        "product_context": {"product": "Synthetic Controller"},
        "facts": {
            "symptom": {
                "value": "设备上电后出现复位。",
                "evidence_block_ids": [by_text["设备上电后出现复位。"]],
            },
            "root_cause": {
                "value": "输入浪涌触发保护。",
                "evidence_block_ids": [by_text["输入浪涌触发保护。"]],
            },
            "actions": {
                "value": "增加输入保护器件。",
                "evidence_block_ids": [by_text["增加输入保护器件。"]],
            },
            "analysis_process": {
                "value": "浪涌测试可复现。",
                "evidence_block_ids": [by_text["测试项 | 结果\n浪涌 | 复现"]],
            },
        },
        "circuit_feature_links": [
            {
                "node_id": "CF-POWER",
                "confidence": 0.91,
                "evidence_block_ids": [by_text["输入浪涌触发保护。"]],
            }
        ],
        "material_links": [
            {
                "node_id": "MD-PROTECT",
                "confidence": 0.86,
                "evidence_block_ids": [by_text["增加输入保护器件。"]],
            }
        ],
    }


def test_m4_docx_parser_preserves_order_sections_table_and_image(tmp_path: Path):
    parsed = parse_docx(_docx(tmp_path))
    assert parsed.source_ref == "word:A1234-电源上电复位.docx"
    assert [block["block_type"] for block in parsed.blocks] == [
        "HEADING",
        "PARAGRAPH",
        "HEADING",
        "PARAGRAPH",
        "IMAGE",
        "TABLE",
        "HEADING",
        "PARAGRAPH",
    ]
    assert parsed.blocks[3]["section_path"] == ["原因分析"]
    assert parsed.blocks[4]["image_ref"] == "media/image1.png"
    assert parsed.blocks[5]["source_locator"]["table"] == 1


def test_m4_identity_uses_a_number_or_stable_generated_id(tmp_path: Path):
    standard = parse_docx(_docx(tmp_path))
    assert derive_identity(standard) == ("A1234", "电源上电复位")

    nonstandard = parse_docx(_docx(tmp_path, "非标准案例.docx"))
    case_id, title = derive_identity(nonstandard)
    assert case_id.startswith("HC-SRC-")
    assert title == "非标准案例"


def test_m4_adapter_creates_candidate_evidence_and_suggested_mappings(tmp_path: Path):
    service = _backend(tmp_path)
    _trees(service)
    result = HardwareCaseAIAdapter(service, _structurer).ingest_docx(_docx(tmp_path))

    assert result["status"] == "SUCCESS"
    case = service.get_case("A1234", role="MAINTAINER")
    assert case["facts"]["root_cause"]["candidate_value"] == "输入浪涌触发保护。"
    assert case["facts"]["root_cause"]["confirmed_value"] is None

    evidence = service.repository.list_evidence("A1234")
    assert len(evidence) == 4
    assert all(item["source_ref"] == "word:A1234-电源上电复位.docx" for item in evidence)

    mappings = service.repository.list_mappings(case_id="A1234")
    assert {item["mapping_status"] for item in mappings} == {"SUGGESTED"}
    assert {item["tree_type"] for item in mappings} == {
        "CIRCUIT_FEATURE",
        "MATERIAL_DEVICE",
    }


def test_m4_ai_candidate_is_not_visible_or_publishable(tmp_path: Path):
    service = _backend(tmp_path)
    _trees(service)
    HardwareCaseAIAdapter(service, _structurer).ingest_docx(_docx(tmp_path))

    assert service.search_cases("输入浪涌")["results"] == []
    gate = service.publish_case("A1234")
    assert gate["passed"] is False
    assert "CORE_FACTS_NOT_REVIEWED" in gate["blockers"]
    assert "NO_CONFIRMED_MAPPING" in gate["blockers"]


def test_m4_bad_evidence_reference_fails_closed_to_needs_review(tmp_path: Path):
    service = _backend(tmp_path)
    _trees(service)

    def bad(document):
        result = _structurer(document)
        result["facts"]["root_cause"]["evidence_block_ids"] = ["B9999"]
        return result

    result = HardwareCaseAIAdapter(service, bad).ingest_docx(_docx(tmp_path))
    assert result["status"] == "NEEDS_REVIEW"
    assert "KEY_FACT_EVIDENCE_MISSING:root_cause" in result["warnings"]
    assert service.get_case("A1234", role="MAINTAINER")["facts"]["root_cause"][
        "confirmed_value"
    ] is None


def test_m4_unknown_mapping_node_is_not_persisted(tmp_path: Path):
    service = _backend(tmp_path)
    _trees(service)

    def bad_mapping(document):
        result = _structurer(document)
        result["circuit_feature_links"][0]["node_id"] = "NO-SUCH-NODE"
        return result

    result = HardwareCaseAIAdapter(service, bad_mapping).ingest_docx(_docx(tmp_path))
    assert result["status"] == "NEEDS_REVIEW"
    assert result["circuit_suggestion_count"] == 0
    assert any("MAPPING_NODE_NOT_FOUND" in item for item in result["warnings"])


def test_m4_structurer_failure_preserves_source_and_no_fake_confirmed_values(tmp_path: Path):
    service = _backend(tmp_path)

    def fail(_document):
        raise RuntimeError("synthetic semantic failure")

    result = HardwareCaseAIAdapter(service, fail).ingest_docx(_docx(tmp_path))
    assert result["status"] == "FAILED"
    case = service.get_case("A1234", role="MAINTAINER")
    assert case["processing_status"] == "STRUCTURE_EXTRACTION_FAILED"
    assert case["source_refs"] == ["word:A1234-电源上电复位.docx"]
    assert case["facts"] == {}


def test_m4_persisted_data_never_contains_local_source_path(tmp_path: Path):
    service = _backend(tmp_path)
    _trees(service)
    path = _docx(tmp_path)
    HardwareCaseAIAdapter(service, _structurer).ingest_docx(path)

    serialized = repr(service.get_case("A1234", role="MAINTAINER"))
    serialized += repr(service.repository.list_evidence("A1234"))
    assert str(tmp_path) not in serialized


def test_m4_mock_golden_path_word_to_publish_to_search_and_evidence(tmp_path: Path):
    service = _backend(tmp_path)
    _trees(service)
    result = HardwareCaseAIAdapter(service, _structurer).ingest_docx(_docx(tmp_path))
    assert result["status"] == "SUCCESS"

    case = service.get_case("A1234", role="MAINTAINER")
    for field_name in ("symptom", "root_cause", "actions"):
        service.review_case(
            "A1234",
            field_name,
            disposition="CONFIRMED",
            confirmed_value=case["facts"][field_name]["candidate_value"],
        )

    suggested = service.repository.list_mappings(case_id="A1234")
    for item in suggested:
        service.set_mapping({**item, "mapping_status": "CONFIRMED"})

    published = service.publish_case("A1234")
    assert published["passed"] is True

    search = service.search_cases("输入浪涌")
    assert search["results"][0]["case_id"] == "A1234"
    evidence = service.get_evidence("A1234")["evidence"]
    assert evidence
    assert evidence[0]["locator"]["block_id"].startswith("B")
