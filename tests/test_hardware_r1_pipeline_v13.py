from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from services.hardware_case_markdown_agent import run_r1_agent_extraction
from services.hardware_case_r1_preview_store import HardwareR1PreviewStore
from services.hardware_case_r1_runtime import map_r1_runtime_error


FACT_FIELDS = (
    "background",
    "symptom",
    "impact",
    "occurrence_condition",
    "analysis_process",
    "failure_mode",
    "root_cause",
    "failure_mechanism",
    "actions",
    "verification_result",
    "conclusion",
)


def field(value=None, refs=None, status=None):
    return {
        "value": value,
        "status": status or ("EXTRACTED" if value is not None else "MISSING"),
        "evidence_block_ids": list(refs or []),
    }


def snapshot():
    blocks = [
        {
            "block_id": "B1",
            "block_type": "PARAGRAPH",
            "text": "本机 MCU → 串口屏经常乱码；PC / 其他 MCU 正常。",
            "source_locator": {"paragraph": 1},
        },
        {
            "block_id": "B2",
            "block_type": "PARAGRAPH",
            "text": "空载 TX ≈3.3V；带载 ≈1.6V。",
            "source_locator": {"paragraph": 2},
        },
        {
            "block_id": "B3",
            "block_type": "PARAGRAPH",
            "text": "MCU TX 默认弱上拉，驱动能力不足。",
            "source_locator": {"paragraph": 3},
        },
        {
            "block_id": "B4",
            "block_type": "PARAGRAPH",
            "text": "弱上拉 → 推挽输出。",
            "source_locator": {"paragraph": 4},
        },
        {
            "block_id": "B5",
            "block_type": "PARAGRAPH",
            "text": "长期可靠性测试未再复现。",
            "source_locator": {"paragraph": 5},
        },
    ]
    return {
        "snapshot_version": "hardware-document-snapshot/v1",
        "source": {
            "source_id": "a0152-source",
            "file_name": "A0152-CPU_串口输出配置程弱上拉导致串口屏乱码.docx",
        },
        "identity": {
            "business_case_id": "A0152",
            "raw_title": "CPU_串口输出配置程弱上拉导致串口屏乱码",
            "identity_status": "PARSED",
            "warnings": [],
        },
        "structure": {
            "blocks": blocks,
            "headings": [],
            "paragraphs": blocks,
            "tables": [],
            "images": [],
        },
        "metadata": {
            "r1_latency_trace": {"PARSE_MS": 7, "MARKDOWN_MS": 2}
        },
    }


def stage_a_payload():
    facts = {name: field() for name in FACT_FIELDS}
    facts.update(
        {
            "symptom": field(
                "本机 MCU → 串口屏经常乱码；PC / 其他 MCU 正常。",
                ["B1"],
            ),
            "analysis_process": field(
                "空载 TX ≈3.3V；带载 ≈1.6V。",
                ["B2"],
            ),
            "root_cause": field(
                "MCU TX 默认弱上拉，驱动能力不足。",
                ["B3"],
            ),
            "actions": field("弱上拉 → 推挽输出。", ["B4"]),
            "verification_result": field(
                "长期可靠性测试未再复现.",
                ["B5"],
            ),
        }
    )
    # Keep the hard-grounded value byte-for-byte aligned with Evidence.
    facts["verification_result"]["value"] = "长期可靠性测试未再复现。"
    return {
        "engineering_context": {
            "primary_subject": field("MCU", ["B1"]),
            "component_or_device": field("MCU", ["B1"]),
            "interface": field("UART", ["B1"]),
            "signal": field("3.3V TTL TX", ["B2"]),
            "peer_device_or_load": field("串口屏", ["B1"]),
            "key_parameters": [
                {
                    "name": "TX unloaded",
                    "value": 3.3,
                    "unit": "V",
                    "status": "EXTRACTED",
                    "evidence_block_ids": ["B2"],
                },
                {
                    "name": "TX loaded",
                    "value": 1.6,
                    "unit": "V",
                    "status": "EXTRACTED",
                    "evidence_block_ids": ["B2"],
                },
            ],
        },
        "facts": facts,
    }


def stage_b_payload():
    missing = {
        "value": None,
        "status": "MISSING",
        "derived_from_fields": [],
        "evidence_block_ids": [],
    }
    return {
        "reusable_knowledge_candidate": {
            "engineering_rule": {
                "value": "UART TX 带载电平不足时应检查输出驱动配置。",
                "status": "EXTRACTED",
                "derived_from_fields": ["root_cause", "actions"],
                "evidence_block_ids": ["B3", "B4"],
            },
            "design_constraint": deepcopy(missing),
            "diagnostic_clue": {
                "value": "空载正常、带载电平明显下降是驱动能力不足的诊断线索。",
                "status": "EXTRACTED",
                "derived_from_fields": ["analysis_process", "root_cause"],
                "evidence_block_ids": ["B2", "B3"],
            },
            "verification_method": {
                "value": "修改输出模式后执行长期可靠性测试。",
                "status": "EXTRACTED",
                "derived_from_fields": ["actions", "verification_result"],
                "evidence_block_ids": ["B4", "B5"],
            },
            "applicability": deepcopy(missing),
            "conclusion": deepcopy(missing),
        }
    }


def runtime_meta(agent, run):
    return {
        "run_id": run,
        "task_id": "task-" + run,
        "agent_id": agent,
        "agent_config_version": "v1",
        "agent_config_hash": "cfg-" + agent,
        "prompt_version": "prompt-v1",
        "provider_call_count": 1,
        "provider_call_ms": [12],
        "prompt_tokens": "UNKNOWN",
        "completion_tokens": "UNKNOWN",
        "validation_retry_count": 0,
        "retry_budget_exhausted": False,
        "cache_hit": False,
    }


class FakePipeline:
    def __init__(
        self,
        *,
        fail_a=False,
        fail_b=False,
        stage_a_data=None,
        stage_b_data=None,
    ):
        self.fail_a = fail_a
        self.fail_b = fail_b
        self.stage_a_data = deepcopy(stage_a_data)
        self.stage_b_data = deepcopy(stage_b_data)
        self.stage_b_input = None
        self.force_flags = []

    def run_stage_a(self, payload, *, source_id, markdown_hash, force_retry=False):
        self.force_flags.append(("A", force_retry))
        if self.fail_a:
            return {
                "ok": False,
                "data": None,
                "error_code": "OUTPUT_SCHEMA_INVALID",
                "raw_error_code": "PROVIDER_SCHEMA_INVALID",
                "runtime": runtime_meta(
                    "hardware_case.r1_case_extract",
                    "run-a-failed",
                ),
            }
        return {
            "ok": True,
            "data": (
                deepcopy(self.stage_a_data)
                if self.stage_a_data is not None
                else stage_a_payload()
            ),
            "runtime": runtime_meta("hardware_case.r1_case_extract", "run-a"),
        }

    def run_stage_b(self, payload, *, source_id, markdown_hash, force_retry=False):
        self.force_flags.append(("B", force_retry))
        self.stage_b_input = payload
        if self.fail_b:
            return {
                "ok": False,
                "data": None,
                "error_code": "PROVIDER_TIMEOUT",
                "raw_error_code": "PROVIDER_TRANSPORT",
                "runtime": runtime_meta(
                    "hardware_case.r1_reuse_derive",
                    "run-b-failed",
                ),
            }
        return {
            "ok": True,
            "data": (
                deepcopy(self.stage_b_data)
                if self.stage_b_data is not None
                else stage_b_payload()
            ),
            "runtime": runtime_meta("hardware_case.r1_reuse_derive", "run-b"),
        }


def test_v13_pipeline_splits_cognition_and_keeps_golden_contract():
    pipeline = FakePipeline()
    result = run_r1_agent_extraction(snapshot(), pipeline)

    assert result["pipeline_status"] == "GOLDEN_PREVIEW_READY"
    assert result["case_extraction"] == "PASS"
    assert result["reusable_knowledge"] == "PASS"
    assert result["knowledge_object"]["contract_version"] == "hardware-case-knowledge-object/v1"
    assert result["knowledge_object"]["identity"]["raw_title"].startswith("CPU_")
    assert result["structured_result"]["engineering_context"]["primary_subject"]["value"] == "MCU"
    assert result["structured_result"]["engineering_context"]["primary_subject"]["confidence"] is None
    assert result["structured_result"]["engineering_context"]["primary_subject"]["warnings"] == []
    assert result["structured_result"]["reusable_knowledge_candidate"]["engineering_rule"]["review_status"] == "UNREVIEWED"

    conflicts = result["structured_result"]["conflicts"]
    assert len(conflicts) == 1
    assert conflicts[0]["type"] == "TITLE_CONTENT_SUBJECT_MISMATCH"
    assert conflicts[0]["resolution_status"] == "NEEDS_REVIEW"

    # Stage B receives only validated Stage A facts/context + necessary Evidence.
    assert "markdown" not in pipeline.stage_b_input
    assert "source_fact" not in pipeline.stage_b_input
    assert set(pipeline.stage_b_input) == {
        "input_contract",
        "engineering_context",
        "facts",
        "evidence_blocks",
    }
    assert result["evidence_validation"]["fabricated_fact_count"] == 0
    assert result["evidence_validation"]["fabricated_block_id_count"] == 0


def test_v13_stage_b_failure_preserves_stage_a():
    result = run_r1_agent_extraction(snapshot(), FakePipeline(fail_b=True))

    assert result["pipeline_status"] == "PARTIAL_REUSABLE_KNOWLEDGE_FAILED"
    assert result["case_extraction"] == "PASS"
    assert result["reusable_knowledge"] == "FAILED"
    assert result["failed_stage"] == "STAGE_B"
    assert result["error_code"] == "PROVIDER_TIMEOUT"
    assert result["stage_a_result"]["facts"]["root_cause"]["value"] == "MCU TX 默认弱上拉，驱动能力不足。"
    assert result["knowledge_object"] is None


def test_v13_force_retry_is_forwarded_to_both_runtime_stages():
    pipeline = FakePipeline()
    result = run_r1_agent_extraction(snapshot(), pipeline, force_retry=True)

    assert result["pipeline_status"] == "GOLDEN_PREVIEW_READY"
    assert pipeline.force_flags == [("A", True), ("B", True)]



def test_v13_stage_a_failure_isolated_and_stage_b_not_run():
    pipeline = FakePipeline(fail_a=True)
    result = run_r1_agent_extraction(snapshot(), pipeline)

    assert result["pipeline_status"] == "CASE_EXTRACTION_FAILED"
    assert result["case_extraction"] == "FAILED"
    assert result["reusable_knowledge"] == "NOT_RUN"
    assert result["failed_stage"] == "STAGE_A"
    assert result["error_code"] == "OUTPUT_SCHEMA_INVALID"
    assert result["markdown_view"]["view_version"] == "hardware-markdown-view/v1"
    assert pipeline.stage_b_input is None
    assert pipeline.force_flags == [("A", False)]


def test_v13_latency_trace_becomes_complete_when_preview_is_saved(tmp_path):
    result = run_r1_agent_extraction(snapshot(), FakePipeline())
    required = {
        "PARSE_MS", "MARKDOWN_MS",
        "STAGE_A_TOTAL_MS", "STAGE_A_PROVIDER_CALL_COUNT",
        "STAGE_A_PROVIDER_CALL_MS", "STAGE_A_PROMPT_TOKENS",
        "STAGE_A_COMPLETION_TOKENS", "STAGE_A_VALIDATION_RETRY_COUNT",
        "CASE_VALIDATION_MS", "CONFLICT_MS",
        "STAGE_B_TOTAL_MS", "STAGE_B_PROVIDER_CALL_COUNT",
        "STAGE_B_PROVIDER_CALL_MS", "STAGE_B_PROMPT_TOKENS",
        "STAGE_B_COMPLETION_TOKENS", "STAGE_B_VALIDATION_RETRY_COUNT",
        "REUSABLE_VALIDATION_MS", "GOLDEN_BUILD_MS",
        "PREVIEW_SAVE_MS", "TOTAL_MS",
    }
    assert required.issubset(result["latency_trace"])
    assert result["latency_trace"]["STAGE_A_PROMPT_TOKENS"] == "UNKNOWN"
    assert result["latency_trace"]["STAGE_B_COMPLETION_TOKENS"] == "UNKNOWN"
    assert result["latency_trace_complete"] is False

    store = HardwareR1PreviewStore(tmp_path / "preview.db")
    saved = store.save(snapshot(), result)
    persisted = store.by_id(saved["preview_id"])["result"]
    assert persisted["latency_trace_complete"] is True
    assert isinstance(persisted["latency_trace"]["PREVIEW_SAVE_MS"], int)
    assert persisted["latency_trace"]["TOTAL_MS"] >= 0


def test_v13_error_contract_mapping_is_specific():
    assert map_r1_runtime_error("MODEL_LOCAL_CONFIG_REQUIRED") == "RUNTIME_CONFIG_MISSING"
    assert map_r1_runtime_error(
        "PROVIDER_TRANSPORT",
        details={"exception_type": "TimeoutError"},
    ) == "PROVIDER_TIMEOUT"
    assert map_r1_runtime_error("PROVIDER_TRANSPORT") == "PROVIDER_TRANSPORT_FAILED"
    assert map_r1_runtime_error(
        "PROVIDER_SCHEMA_INVALID",
        category="VALIDATION",
        validation_retry_count=0,
    ) == "OUTPUT_SCHEMA_INVALID"
    assert map_r1_runtime_error(
        "PROVIDER_SCHEMA_INVALID",
        category="VALIDATION",
        validation_retry_count=1,
    ) == "VALIDATION_RETRY_EXHAUSTED"
    assert map_r1_runtime_error("RETRY_BUDGET_EXHAUSTED") == "PROVIDER_CALL_BUDGET_EXHAUSTED"
    assert map_r1_runtime_error("SOMETHING_ELSE") == "RUNTIME_EXECUTION_FAILED"

def test_v131_key_parameters_aggregate_traceability_for_design_constraint():
    payload = stage_b_payload()
    payload["reusable_knowledge_candidate"]["design_constraint"] = {
        "value": "UART TX 带载电平设计必须满足外部负载所需驱动能力。",
        "status": "EXTRACTED",
        "derived_from_fields": ["engineering_context.key_parameters"],
        "evidence_block_ids": ["B2"],
    }

    result = run_r1_agent_extraction(
        snapshot(),
        FakePipeline(stage_b_data=payload),
    )

    assert result["pipeline_status"] == "GOLDEN_PREVIEW_READY"
    assert result["evidence_validation"]["status"] == "PASS"
    assert not any(
        item.startswith("REUSABLE_DERIVED_FIELD_UNKNOWN:design_constraint")
        for item in result["evidence_validation"]["errors"]
    )
    assert not any(
        item == "REUSABLE_EVIDENCE_NOT_TRACEABLE:design_constraint"
        for item in result["evidence_validation"]["errors"]
    )


def test_v131_key_parameters_aggregate_traceability_for_verification_method():
    payload = stage_b_payload()
    payload["reusable_knowledge_candidate"]["verification_method"] = {
        "value": "修改输出模式后验证带载电平并执行长期可靠性测试。",
        "status": "EXTRACTED",
        "derived_from_fields": [
            "engineering_context.key_parameters",
            "verification_result",
        ],
        "evidence_block_ids": ["B2", "B5"],
    }

    result = run_r1_agent_extraction(
        snapshot(),
        FakePipeline(stage_b_data=payload),
    )

    assert result["pipeline_status"] == "GOLDEN_PREVIEW_READY"
    assert result["evidence_validation"]["status"] == "PASS"
    assert "REUSABLE_EVIDENCE_NOT_TRACEABLE:verification_method" not in (
        result["evidence_validation"]["errors"]
    )


def test_v131_a0152_primary_subject_is_body_semantic_and_title_conflict_is_local():
    result = run_r1_agent_extraction(snapshot(), FakePipeline())

    assert (
        result["structured_result"]["engineering_context"]["primary_subject"]["value"]
        == "MCU"
    )
    assert result["knowledge_object"]["identity"]["raw_title"].startswith("CPU_")
    conflicts = result["structured_result"]["conflicts"]
    assert any(
        item["type"] == "TITLE_CONTENT_SUBJECT_MISMATCH"
        and item["resolution_status"] == "NEEDS_REVIEW"
        for item in conflicts
    )

    prompt = (
        Path(__file__).resolve().parents[1]
        / "prompts/runtime/hardware_case/r1_case_extract_v1.md"
    ).read_text(encoding="utf-8")
    assert "Never copy raw_title into primary_subject" in prompt
    assert "body-supported subject" in prompt


def test_v131_a0152_impact_boundary_requires_missing_without_explicit_consequence():
    result = run_r1_agent_extraction(snapshot(), FakePipeline())
    impact = result["structured_result"]["facts"]["impact"]

    assert impact["value"] is None
    assert impact["extraction_status"] == "MISSING"
    assert impact["evidence_block_ids"] == []

    prompt = (
        Path(__file__).resolve().parents[1]
        / "prompts/runtime/hardware_case/r1_case_extract_v1.md"
    ).read_text(encoding="utf-8")
    assert "Never paraphrase or copy symptom into impact" in prompt
    assert "status=MISSING" in prompt

