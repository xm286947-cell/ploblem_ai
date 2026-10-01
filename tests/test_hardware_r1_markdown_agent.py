from __future__ import annotations

from services.hardware_case_markdown_agent import (
    build_markdown_view,
    normalize_extraction_v2,
    run_r1_agent_extraction,
    validate_agent_result,
)


def snapshot() -> dict:
    blocks = [
        {
            "block_id": "B0001",
            "block_type": "PARAGRAPH",
            "text": "二、问题描述",
            "style": None,
            "section_path": [],
            "source_locator": {"paragraph": 1, "block_id": "B0001"},
            "image_ref": None,
            "table_rows": None,
        },
        {
            "block_id": "B0002",
            "block_type": "PARAGRAPH",
            "text": "MCU 给串口屏发送数据时经常出现乱码。",
            "style": None,
            "section_path": [],
            "source_locator": {"paragraph": 2, "block_id": "B0002"},
            "image_ref": None,
            "table_rows": None,
        },
        {
            "block_id": "B0003",
            "block_type": "PARAGRAPH",
            "text": "弱上拉模式驱动能力不足，TX 高电平被拉低。",
            "style": None,
            "section_path": [],
            "source_locator": {"paragraph": 3, "block_id": "B0003"},
            "image_ref": None,
            "table_rows": None,
        },
        {
            "block_id": "B0004",
            "block_type": "TABLE",
            "text": "模式 | 电流\n推挽 | 20mA\n弱上拉 | 1mA",
            "style": None,
            "section_path": [],
            "source_locator": {"table": 1, "block_id": "B0004"},
            "image_ref": None,
            "table_rows": [["模式", "电流"], ["推挽", "20mA"], ["弱上拉", "1mA"]],
        },
        {
            "block_id": "B0005",
            "block_type": "PARAGRAPH",
            "text": "将弱上拉修改为推挽输出模式，长期测试未再复现。",
            "style": None,
            "section_path": [],
            "source_locator": {"paragraph": 4, "block_id": "B0005"},
            "image_ref": None,
            "table_rows": None,
        },
    ]
    return {
        "snapshot_version": "hardware-document-snapshot/v1",
        "source": {
            "source_id": "0" * 64,
            "source_ref": "word:A0152-synthetic.docx",
            "file_name": "A0152-synthetic.docx",
        },
        "identity": {
            "business_case_id": "A0152",
            "raw_title": "synthetic",
            "source_id": "0" * 64,
            "identity_status": "PARSED",
            "warnings": [],
        },
        "structure": {
            "headings": [],
            "paragraphs": [blocks[0], blocks[1], blocks[2], blocks[4]],
            "tables": [blocks[3]],
            "images": [],
            "blocks": blocks,
        },
        "counts": {"headings": 0, "paragraphs": 4, "tables": 1, "images": 0, "blocks": 5},
    }


def good_agent_result() -> dict:
    return {
        "title": "串口屏乱码",
        "product_context": {},
        "facts": {
            "symptom": {
                "value": "MCU 给串口屏发送数据时经常出现乱码。",
                "evidence_block_ids": ["B0002"],
            },
            "root_cause": {
                "value": "弱上拉模式驱动能力不足，TX 高电平被拉低。",
                "evidence_block_ids": ["B0003"],
            },
            "actions": {
                "value": "将弱上拉修改为推挽输出模式，长期测试未再复现。",
                "evidence_block_ids": ["B0005"],
            },
        },
        "circuit_feature_links": [],
        "material_links": [],
    }


def test_markdown_view_preserves_plain_paragraphs_order_table_and_locators():
    view = build_markdown_view(snapshot())
    markdown = view["markdown"]

    assert view["view_version"] == "hardware-markdown-view/v1"
    assert view["block_order"] == ["B0001", "B0002", "B0003", "B0004", "B0005"]
    assert markdown.index("HC_BLOCK B0001") < markdown.index("HC_BLOCK B0002")
    assert "二、问题描述" in markdown
    assert "# 二、问题描述" not in markdown
    assert "| 模式 | 电流 |" in markdown
    assert "| 弱上拉 | 1mA |" in markdown
    assert 'LOCATOR={"block_id":"B0004","table":1}' in markdown


def test_evidence_gate_passes_grounded_result():
    validation = validate_agent_result(snapshot(), normalize_extraction_v2(good_agent_result()))
    assert validation["status"] == "PASS"
    assert validation["fabricated_fact_count"] == 0
    assert {item["block_id"] for item in validation["evidence"]} == {
        "B0002",
        "B0003",
        "B0005",
    }


def test_evidence_gate_fails_closed_on_fake_block_unsupported_fact_and_mapping():
    result = good_agent_result()
    result["facts"]["root_cause"] = {
        "value": "电池热失控导致系统烧毁。",
        "evidence_block_ids": ["B9999"],
    }
    result["facts"]["symptom"] = {
        "value": "电池热失控导致系统烧毁。",
        "evidence_block_ids": ["B0002"],
    }
    result["circuit_feature_links"] = [
        {"node_id": "FAKE", "confidence": 1.0, "evidence_block_ids": ["B0002"]}
    ]
    validation = validate_agent_result(snapshot(), normalize_extraction_v2(result))

    assert validation["status"] == "FAIL"
    assert "EVIDENCE_BLOCK_NOT_FOUND:root_cause:B9999" in validation["errors"]
    assert "FABRICATED_OR_UNSUPPORTED_FACT:symptom" in validation["errors"]
    assert validation["fabricated_fact_count"] == 1


def test_r1_agent_runtime_input_is_compact_and_keeps_evidence_blocks():
    captured = {}

    def structurer(document):
        captured.update(document)
        return good_agent_result()

    result = run_r1_agent_extraction(snapshot(), structurer)

    assert captured["input_contract"] == "hardware-case-r1-agent-input/v2"
    assert "markdown_view" not in captured
    assert "markdown_text" not in captured
    assert "blocks" not in captured
    assert "valid_block_ids" not in captured
    assert "HC_BLOCK B0001" in captured["markdown"]
    assert "tree_candidates" not in captured
    assert [item["block_id"] for item in captured["evidence_blocks"]] == ["B0001", "B0002", "B0003", "B0004", "B0005"]
    assert set(captured["evidence_blocks"][0]) == {"block_id", "block_type", "text", "source_locator"}
    assert result["status"] == "PASS"
    assert result["evidence_validation"]["fabricated_fact_count"] == 0
