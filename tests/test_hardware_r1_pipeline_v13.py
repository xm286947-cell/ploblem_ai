from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import hashlib
import json
import sqlite3

from services.hardware_case_markdown_agent import run_r1_agent_extraction
from services.hardware_case_r1_preview_store import HardwareR1PreviewStore
from services.hardware_case_r1_runtime import (
    R1_CACHE_KEY_VERSION,
    R1_PIPELINE_VERSION,
    R1_STAGE_A_SCHEMA_VERSION,
    R1_STAGE_A_VALIDATOR_VERSION,
    R1_STAGE_B_SCHEMA_VERSION,
    R1_STAGE_B_VALIDATOR_VERSION,
    _R1StageCache,
    _stage_cache_key_v2,
    map_r1_runtime_error,
)


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

def test_v1321_a0152_translated_hard_grounded_facts_fail_as_language_mismatch_not_fabrication():
    payload = stage_a_payload()
    payload["facts"]["symptom"] = field(
        "When the MCU sent data to the serial screen, garbled characters often appeared.",
        ["B1"],
    )
    payload["facts"]["root_cause"] = field(
        "The MCU TX port was configured in weak pull-up mode with insufficient drive capability.",
        ["B3"],
    )
    payload["facts"]["actions"] = field(
        "Change the MCU output mode from weak pull-up to push-pull.",
        ["B4"],
    )
    payload["facts"]["verification_result"] = field(
        "The issue did not recur after long-term reliability testing.",
        ["B5"],
    )

    result = run_r1_agent_extraction(
        snapshot(),
        FakePipeline(stage_a_data=payload),
    )

    assert result["pipeline_status"] == "CASE_EXTRACTION_FAILED"
    assert result["error_code"] == "EVIDENCE_VALIDATION_FAILED"
    errors = result["evidence_validation"]["errors"]
    assert "HARD_GROUNDED_LANGUAGE_MISMATCH:symptom" in errors
    assert "HARD_GROUNDED_LANGUAGE_MISMATCH:root_cause" in errors
    assert "HARD_GROUNDED_LANGUAGE_MISMATCH:actions" in errors
    assert "HARD_GROUNDED_LANGUAGE_MISMATCH:verification_result" in errors
    assert result["evidence_validation"]["fabricated_fact_count"] == 0


def test_v1321_stage_a_prompt_requires_source_language_for_hard_grounded_fields():
    prompt = (
        Path(__file__).resolve().parents[1]
        / "prompts/runtime/hardware_case/r1_case_extract_v1.md"
    ).read_text(encoding="utf-8")

    assert "Preserve the source document language" in prompt
    assert "output Chinese rather than translating it to" in prompt
    assert "symptom, root_cause, actions, and" in prompt
    assert "verification_result" in prompt

class FoundationCachePipeline:
    """Small cache-aware fake that exercises Pipeline cache lifecycle semantics."""

    def __init__(
        self,
        *,
        cache_a=None,
        cache_b=None,
        provider_a=None,
        provider_b=None,
        fail_a=False,
        fail_b=False,
    ):
        self.cache_a = deepcopy(cache_a)
        self.cache_b = deepcopy(cache_b)
        self.provider_a = deepcopy(provider_a)
        self.provider_b = deepcopy(provider_b)
        self.fail_a = fail_a
        self.fail_b = fail_b
        self.provider_calls = {"A": 0, "B": 0}
        self.commits = []
        self.rejects = []
        self.stage_b_input = None

    @staticmethod
    def _meta(agent, run, *, cache_hit=False, rejected=False, calls=1, stage_b_input_hash=None):
        meta = runtime_meta(agent, run)
        meta.update(
            {
                "provider_call_count": calls,
                "cache_hit": cache_hit,
                "cache_rejected_by_validator": rejected,
                "cached_from_run_id": run if cache_hit else None,
                "cache_key_version": "v2",
            }
        )
        if stage_b_input_hash is not None:
            meta["stage_b_input_hash"] = stage_b_input_hash
        return meta

    def run_stage_a(
        self,
        payload,
        *,
        source_id,
        markdown_hash,
        force_retry=False,
        bypass_cache=False,
        cache_rejected_by_validator=False,
    ):
        if self.cache_a is not None and not force_retry and not bypass_cache:
            return {
                "ok": True,
                "data": deepcopy(self.cache_a),
                "runtime": self._meta(
                    "hardware_case.r1_case_extract",
                    "run-a-cache",
                    cache_hit=True,
                    calls=0,
                ),
                "cache_key": "cache-a",
                "cache_write_pending": False,
            }
        self.provider_calls["A"] += 1
        if self.fail_a:
            return {
                "ok": False,
                "data": None,
                "error_code": "PROVIDER_TIMEOUT",
                "runtime": self._meta(
                    "hardware_case.r1_case_extract",
                    "run-a-failed",
                    rejected=cache_rejected_by_validator,
                    calls=1,
                ),
                "cache_key": "cache-a",
                "cache_write_pending": False,
            }
        return {
            "ok": True,
            "data": deepcopy(
                self.provider_a
                if self.provider_a is not None
                else stage_a_payload()
            ),
            "runtime": self._meta(
                "hardware_case.r1_case_extract",
                "run-a-provider",
                rejected=cache_rejected_by_validator,
                calls=1,
            ),
            "cache_key": "cache-a",
            "cache_write_pending": True,
        }

    def run_stage_b(
        self,
        payload,
        *,
        source_id,
        markdown_hash=None,
        force_retry=False,
        bypass_cache=False,
        cache_rejected_by_validator=False,
    ):
        self.stage_b_input = deepcopy(payload)
        input_hash = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if self.cache_b is not None and not force_retry and not bypass_cache:
            return {
                "ok": True,
                "data": deepcopy(self.cache_b),
                "runtime": self._meta(
                    "hardware_case.r1_reuse_derive",
                    "run-b-cache",
                    cache_hit=True,
                    calls=0,
                    stage_b_input_hash=input_hash,
                ),
                "cache_key": "cache-b",
                "cache_write_pending": False,
            }
        self.provider_calls["B"] += 1
        if self.fail_b:
            return {
                "ok": False,
                "data": None,
                "error_code": "PROVIDER_TIMEOUT",
                "runtime": self._meta(
                    "hardware_case.r1_reuse_derive",
                    "run-b-failed",
                    rejected=cache_rejected_by_validator,
                    calls=1,
                    stage_b_input_hash=input_hash,
                ),
                "cache_key": "cache-b",
                "cache_write_pending": False,
            }
        return {
            "ok": True,
            "data": deepcopy(
                self.provider_b
                if self.provider_b is not None
                else stage_b_payload()
            ),
            "runtime": self._meta(
                "hardware_case.r1_reuse_derive",
                "run-b-provider",
                rejected=cache_rejected_by_validator,
                calls=1,
                stage_b_input_hash=input_hash,
            ),
            "cache_key": "cache-b",
            "cache_write_pending": True,
        }

    def commit_stage_success(self, stage, stage_result):
        if not stage_result.get("cache_write_pending"):
            return False
        if stage == "STAGE_A":
            self.cache_a = deepcopy(stage_result["data"])
        elif stage == "STAGE_B":
            self.cache_b = deepcopy(stage_result["data"])
        else:
            raise AssertionError(stage)
        self.commits.append(stage)
        stage_result["cache_write_pending"] = False
        return True

    def reject_stage_cache(self, stage, stage_result):
        self.rejects.append(stage)
        if stage == "STAGE_A":
            self.cache_a = None
        elif stage == "STAGE_B":
            self.cache_b = None
        else:
            raise AssertionError(stage)
        return True


def _translated_invalid_stage_a():
    payload = stage_a_payload()
    payload["facts"]["symptom"] = field(
        "When the MCU sent data to the serial screen, garbled characters often appeared.",
        ["B1"],
    )
    return payload


def _invalid_stage_b():
    payload = stage_b_payload()
    payload["reusable_knowledge_candidate"]["engineering_rule"] = {
        "value": "无效派生",
        "status": "EXTRACTED",
        "derived_from_fields": ["unknown.field"],
        "evidence_block_ids": ["B3"],
    }
    return payload


def test_v133_invalid_stage_a_is_never_cached():
    pipeline = FoundationCachePipeline(provider_a=_translated_invalid_stage_a())
    result = run_r1_agent_extraction(snapshot(), pipeline)

    assert result["pipeline_status"] == "CASE_EXTRACTION_FAILED"
    assert result["error_code"] == "EVIDENCE_VALIDATION_FAILED"
    assert "STAGE_A" not in pipeline.commits
    assert pipeline.cache_a is None


def test_v133_invalid_stage_b_is_never_cached():
    pipeline = FoundationCachePipeline(provider_b=_invalid_stage_b())
    result = run_r1_agent_extraction(snapshot(), pipeline)

    assert result["pipeline_status"] == "PARTIAL_REUSABLE_KNOWLEDGE_FAILED"
    assert result["error_code"] == "REUSABLE_TRACEABILITY_INVALID"
    assert "STAGE_A" in pipeline.commits
    assert "STAGE_B" not in pipeline.commits
    assert pipeline.cache_b is None


def test_v133_valid_cache_hits_are_revalidated_and_use_zero_provider_calls():
    pipeline = FoundationCachePipeline(
        cache_a=stage_a_payload(),
        cache_b=stage_b_payload(),
    )
    result = run_r1_agent_extraction(snapshot(), pipeline)

    assert result["pipeline_status"] == "GOLDEN_PREVIEW_READY"
    assert pipeline.provider_calls == {"A": 0, "B": 0}
    assert result["latency_trace"]["STAGE_A_CACHE_HIT"] is True
    assert result["latency_trace"]["STAGE_B_CACHE_HIT"] is True
    assert result["provider_call_count"] == 0


def test_v133_bad_stage_a_cache_is_evicted_then_provider_recovers():
    pipeline = FoundationCachePipeline(
        cache_a=_translated_invalid_stage_a(),
        provider_a=stage_a_payload(),
    )
    result = run_r1_agent_extraction(snapshot(), pipeline)

    assert result["pipeline_status"] == "GOLDEN_PREVIEW_READY"
    assert pipeline.rejects == ["STAGE_A"]
    assert pipeline.provider_calls["A"] == 1
    assert result["latency_trace"]["STAGE_A_CACHE_HIT"] is True
    assert result["latency_trace"]["STAGE_A_CACHE_REJECTED_BY_VALIDATOR"] is True
    assert result["latency_trace"]["STAGE_A_PROVIDER_CALL_COUNT"] == 1


def test_v133_bad_stage_b_cache_is_evicted_then_provider_recovers():
    pipeline = FoundationCachePipeline(
        cache_a=stage_a_payload(),
        cache_b=_invalid_stage_b(),
        provider_b=stage_b_payload(),
    )
    result = run_r1_agent_extraction(snapshot(), pipeline)

    assert result["pipeline_status"] == "GOLDEN_PREVIEW_READY"
    assert pipeline.rejects == ["STAGE_B"]
    assert pipeline.provider_calls["B"] == 1
    assert result["latency_trace"]["STAGE_B_CACHE_HIT"] is True
    assert result["latency_trace"]["STAGE_B_CACHE_REJECTED_BY_VALIDATOR"] is True
    assert result["latency_trace"]["STAGE_B_PROVIDER_CALL_COUNT"] == 1
    assert result["error_code"] is None


def test_v133_force_retry_failure_preserves_last_good_stage_b_cache():
    last_good = stage_b_payload()
    pipeline = FoundationCachePipeline(
        cache_a=stage_a_payload(),
        cache_b=last_good,
        fail_b=True,
    )
    result = run_r1_agent_extraction(snapshot(), pipeline, force_retry=True)

    assert result["pipeline_status"] == "PARTIAL_REUSABLE_KNOWLEDGE_FAILED"
    assert pipeline.cache_b == last_good
    assert "STAGE_B" not in pipeline.commits


def test_v133_cache_key_v2_invalidates_on_stage_b_input_validator_and_pipeline_change():
    common = {
        "stage": "STAGE_B",
        "source_id": "source-1",
        "input_hash_name": "stage_b_input_hash",
        "agent_config_hash": "agent-cfg",
        "prompt_version": "prompt-v1",
        "schema_version": R1_STAGE_B_SCHEMA_VERSION,
    }
    base = _stage_cache_key_v2(
        **common,
        input_hash="input-a",
        pipeline_version=R1_PIPELINE_VERSION,
        validator_version=R1_STAGE_B_VALIDATOR_VERSION,
    )
    changed_input = _stage_cache_key_v2(
        **common,
        input_hash="input-b",
        pipeline_version=R1_PIPELINE_VERSION,
        validator_version=R1_STAGE_B_VALIDATOR_VERSION,
    )
    changed_validator = _stage_cache_key_v2(
        **common,
        input_hash="input-a",
        pipeline_version=R1_PIPELINE_VERSION,
        validator_version=R1_STAGE_B_VALIDATOR_VERSION + ".next",
    )
    changed_pipeline = _stage_cache_key_v2(
        **common,
        input_hash="input-a",
        pipeline_version=R1_PIPELINE_VERSION + ".next",
        validator_version=R1_STAGE_B_VALIDATOR_VERSION,
    )

    assert R1_CACHE_KEY_VERSION == "v2"
    assert len({base, changed_input, changed_validator, changed_pipeline}) == 4


def test_v133_stage_a_cache_key_contains_markdown_and_version_boundaries():
    base = _stage_cache_key_v2(
        stage="STAGE_A",
        source_id="source-1",
        input_hash_name="markdown_hash",
        input_hash="markdown-a",
        agent_config_hash="agent-cfg",
        prompt_version="prompt-v1",
        pipeline_version=R1_PIPELINE_VERSION,
        validator_version=R1_STAGE_A_VALIDATOR_VERSION,
        schema_version=R1_STAGE_A_SCHEMA_VERSION,
    )
    changed = _stage_cache_key_v2(
        stage="STAGE_A",
        source_id="source-1",
        input_hash_name="markdown_hash",
        input_hash="markdown-b",
        agent_config_hash="agent-cfg",
        prompt_version="prompt-v1",
        pipeline_version=R1_PIPELINE_VERSION,
        validator_version=R1_STAGE_A_VALIDATOR_VERSION,
        schema_version=R1_STAGE_A_SCHEMA_VERSION,
    )
    assert base != changed


def test_v133_preview_clear_does_not_clear_stage_cache_or_runtime_audit(tmp_path):
    runtime_db = tmp_path / "runtime.db"
    stage_cache = _R1StageCache(runtime_db)
    stage_cache.put(
        "STAGE_A",
        "cache-key-a",
        stage_a_payload(),
        {"run_id": "run-good-a"},
        validator_version=R1_STAGE_A_VALIDATOR_VERSION,
        pipeline_version=R1_PIPELINE_VERSION,
        schema_version=R1_STAGE_A_SCHEMA_VERSION,
        agent_config_hash="cfg-a",
        prompt_version="prompt-a",
    )
    with sqlite3.connect(runtime_db) as connection:
        connection.execute(
            "CREATE TABLE runtime_audit_sentinel (run_id TEXT PRIMARY KEY)"
        )
        connection.execute(
            "INSERT INTO runtime_audit_sentinel(run_id) VALUES ('run-audit-keep')"
        )

    preview_store = HardwareR1PreviewStore(tmp_path / "preview.db")
    preview_store.save(snapshot(), {"status": "PASS", "runtime": {"run_id": "preview-run"}})
    assert preview_store.clear() == 1

    assert stage_cache.get("STAGE_A", "cache-key-a") is not None
    with sqlite3.connect(runtime_db) as connection:
        row = connection.execute(
            "SELECT run_id FROM runtime_audit_sentinel"
        ).fetchone()
    assert row == ("run-audit-keep",)

