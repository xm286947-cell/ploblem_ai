from __future__ import annotations

from services.hardware_case_markdown_agent import (
    KNOWLEDGE_OBJECT_VERSION,
    R1_EXTRACTION_CONTRACT_VERSION,
    build_markdown_view,
    run_r1_agent_extraction,
)
from services.hardware_case_r1_runtime import HARDWARE_R1_EXTRACTION_V2_SCHEMA


def synthetic_a0152_snapshot() -> dict:
    blocks = [
        {
            "block_id": "B0001",
            "block_type": "PARAGRAPH",
            "text": "MCU通过UART连接串口屏，TX为3.3V TTL信号。",
            "source_locator": {"paragraph": 1, "block_id": "B0001"},
        },
        {
            "block_id": "B0002",
            "block_type": "PARAGRAPH",
            "text": "MCU连接串口屏发送时经常乱码。",
            "source_locator": {"paragraph": 2, "block_id": "B0002"},
        },
        {
            "block_id": "B0003",
            "block_type": "PARAGRAPH",
            "text": "示波器测得空载TX高电平约3.3V，带载后约1.6V。",
            "source_locator": {"paragraph": 3, "block_id": "B0003"},
        },
        {
            "block_id": "B0004",
            "block_type": "PARAGRAPH",
            "text": "MCU TX 默认弱上拉，驱动能力不足。",
            "source_locator": {"paragraph": 4, "block_id": "B0004"},
        },
        {
            "block_id": "B0005",
            "block_type": "PARAGRAPH",
            "text": "将弱上拉改为推挽输出。",
            "source_locator": {"paragraph": 5, "block_id": "B0005"},
        },
        {
            "block_id": "B0006",
            "block_type": "PARAGRAPH",
            "text": "长期可靠性测试未再复现。",
            "source_locator": {"paragraph": 6, "block_id": "B0006"},
        },
        {
            "block_id": "B0007",
            "block_type": "TABLE",
            "text": "输出模式 | 最大电流\n推挽 | 20mA\n弱上拉 | 1mA",
            "table_rows": [["输出模式", "最大电流"], ["推挽", "20mA"], ["弱上拉", "1mA"]],
            "source_locator": {"table": 1, "block_id": "B0007"},
        },
    ]
    return {
        "snapshot_version": "hardware-document-snapshot/v1",
        "source": {
            "source_id": "a" * 64,
            "source_ref": "word:A0152-synthetic.docx",
            "file_name": "A0152-CPU_串口输出配置弱上拉导致乱码.docx",
        },
        "identity": {
            "business_case_id": "A0152",
            "raw_title": "CPU_串口输出配置弱上拉导致乱码",
            "source_id": "a" * 64,
            "identity_status": "PARSED",
            "warnings": [],
        },
        "structure": {
            "headings": [],
            "paragraphs": blocks[:6],
            "tables": [blocks[6]],
            "images": [],
            "blocks": blocks,
        },
        "counts": {"headings": 0, "paragraphs": 6, "tables": 1, "images": 0, "blocks": 7},
    }


def c(value, refs, status="EXTRACTED"):
    return {
        "value": value,
        "extraction_status": status,
        "evidence_block_ids": refs,
        "confidence": 0.95 if status == "EXTRACTED" else None,
        "warnings": [],
    }


def missing():
    return c(None, [], "MISSING")


def reusable(value, refs, derived):
    return {
        **c(value, refs),
        "derived_from_fields": derived,
        "review_status": "UNREVIEWED",
    }


def extraction_v2() -> dict:
    return {
        "contract_version": R1_EXTRACTION_CONTRACT_VERSION,
        "engineering_context": {
            "primary_subject": c("MCU", ["B0001"]),
            "component_or_device": c("MCU", ["B0001"]),
            "interface": c("UART", ["B0001"]),
            "signal": c("3.3V TTL TX", ["B0001"]),
            "peer_device_or_load": c("串口屏", ["B0001"]),
            "key_parameters": [
                {
                    "name": "unloaded_tx_high",
                    **c("约3.3V", ["B0003"]),
                    "unit": "V",
                },
                {
                    "name": "loaded_tx_high",
                    **c("约1.6V", ["B0003"]),
                    "unit": "V",
                },
                {
                    "name": "push_pull_max_current",
                    **c("20mA", ["B0007"]),
                    "unit": "mA",
                },
                {
                    "name": "weak_pullup_max_current",
                    **c("1mA", ["B0007"]),
                    "unit": "mA",
                },
            ],
        },
        "facts": {
            "background": c("MCU通过UART连接串口屏，TX为3.3V TTL信号。", ["B0001"]),
            "symptom": c("MCU连接串口屏发送时经常乱码。", ["B0002"]),
            "impact": missing(),
            "occurrence_condition": c("示波器测得空载TX高电平约3.3V，带载后约1.6V。", ["B0003"]),
            "analysis_process": c("示波器测得空载TX高电平约3.3V，带载后约1.6V。", ["B0003"]),
            "failure_mode": c("MCU连接串口屏发送时经常乱码。", ["B0002"]),
            "root_cause": c("MCU TX 默认弱上拉，驱动能力不足。", ["B0004"]),
            "failure_mechanism": c("MCU TX 默认弱上拉，驱动能力不足。", ["B0004"]),
            "actions": c("将弱上拉改为推挽输出。", ["B0005"]),
            "verification_result": c("长期可靠性测试未再复现。", ["B0006"]),
            "conclusion": c("MCU TX 默认弱上拉，驱动能力不足。", ["B0004"]),
        },
        "conflicts": [],
        "reusable_knowledge_candidate": {
            "engineering_rule": reusable(
                "数字输出设计需同时校核输出驱动能力和负载要求。",
                ["B0004", "B0007"],
                ["root_cause", "engineering_context.key_parameters.push_pull_max_current"],
            ),
            "design_constraint": reusable(
                "输出模式应与外部负载匹配。",
                ["B0004", "B0005"],
                ["root_cause", "actions"],
            ),
            "diagnostic_clue": reusable(
                "空载正常、带载电平下降时优先检查输出模式与驱动能力。",
                ["B0003", "B0004"],
                ["analysis_process", "root_cause"],
            ),
            "verification_method": reusable(
                "对比空载和带载波形并在修改后做长期验证。",
                ["B0003", "B0006"],
                ["analysis_process", "verification_result"],
            ),
            "applicability": reusable(
                "适用于数字输出驱动外部负载的类似场景。",
                ["B0001", "B0004"],
                ["engineering_context.interface", "root_cause"],
            ),
            "conclusion": reusable(
                "驱动能力不足可导致带载电平下降和通信异常。",
                ["B0002", "B0003", "B0004"],
                ["symptom", "analysis_process", "root_cause"],
            ),
        },
    }


def test_extraction_v2_schema_declares_golden_layers():
    assert HARDWARE_R1_EXTRACTION_V2_SCHEMA["properties"]["contract_version"]["enum"] == [
        "hardware-r1-extraction/v2"
    ]
    assert "engineering_context" in HARDWARE_R1_EXTRACTION_V2_SCHEMA["required"]
    assert "reusable_knowledge_candidate" in HARDWARE_R1_EXTRACTION_V2_SCHEMA["required"]
    assert "conflicts" in HARDWARE_R1_EXTRACTION_V2_SCHEMA["required"]


def test_synthetic_a0152_detects_title_body_conflict_and_preserves_source_fact():
    snapshot = synthetic_a0152_snapshot()

    def structurer(_document):
        return extraction_v2()

    result = run_r1_agent_extraction(snapshot, structurer)
    ko = result["knowledge_object"]

    assert result["status"] == "PASS"
    assert ko["contract_version"] == KNOWLEDGE_OBJECT_VERSION
    assert ko["source_fact"]["raw_title"] == "CPU_串口输出配置弱上拉导致乱码"
    assert ko["engineering_context"]["primary_subject"]["value"] == "MCU"
    assert ko["engineering_context"]["interface"]["value"] == "UART"
    assert ko["engineering_context"]["signal"]["value"] == "3.3V TTL TX"
    assert ko["engineering_context"]["peer_device_or_load"]["value"] == "串口屏"

    conflict = ko["conflicts"][0]
    assert conflict["type"] == "TITLE_CONTENT_SUBJECT_MISMATCH"
    assert conflict["status"] == "OPEN"
    assert conflict["resolution_status"] == "NEEDS_REVIEW"
    assert conflict["source_values"][0] == {"source": "SOURCE_RAW_TITLE", "value": "CPU"}
    assert conflict["source_values"][1] == {"source": "AI_BODY_CANDIDATE", "value": "MCU"}
    assert conflict["evidence_block_ids"] == ["B0001"]


def test_reusable_knowledge_is_candidate_and_traceable_to_case_evidence():
    result = run_r1_agent_extraction(
        synthetic_a0152_snapshot(),
        lambda _document: extraction_v2(),
    )
    ko = result["knowledge_object"]
    validation = result["evidence_validation"]

    assert validation["status"] == "PASS"
    assert validation["fabricated_block_id_count"] == 0
    assert validation["fabricated_fact_count"] == 0
    assert ko["review"]["object_status"] == "CANDIDATE"
    for field in ko["reusable_knowledge"].values():
        assert field["review_status"] == "UNREVIEWED"
        assert field["derived_from_fields"]
        assert field["evidence_block_ids"]


def test_reusable_knowledge_fails_closed_on_untraceable_evidence():
    payload = extraction_v2()
    payload["reusable_knowledge_candidate"]["engineering_rule"]["evidence_block_ids"] = ["B0006"]

    result = run_r1_agent_extraction(
        synthetic_a0152_snapshot(),
        lambda _document: payload,
    )

    assert result["status"] == "NEEDS_REVIEW"
    assert "REUSABLE_EVIDENCE_NOT_TRACEABLE:engineering_rule" in result["evidence_validation"]["errors"]


def test_golden_preview_provenance_carries_runtime_identity_when_available():
    def structurer(_document):
        return {
            **extraction_v2(),
            "__runtime_meta__": {
                "run_id": "run-synthetic",
                "task_id": "task-synthetic",
                "agent_id": "hardware_case.r1_extract",
                "agent_config_version": "v2",
            },
        }

    result = run_r1_agent_extraction(synthetic_a0152_snapshot(), structurer)
    provenance = result["knowledge_object"]["provenance"]

    assert provenance == {
        "source_id": "a" * 64,
        "snapshot_version": "hardware-document-snapshot/v1",
        "markdown_version": "hardware-markdown-view/v1",
        "agent_id": "hardware_case.r1_extract",
        "agent_config_version": "v2",
        "runtime_run_id": "run-synthetic",
        "extraction_contract_version": "hardware-r1-extraction/v2",
    }
