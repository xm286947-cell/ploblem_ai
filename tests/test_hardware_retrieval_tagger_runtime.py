from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.request import Request, urlopen

from services.hardware_retrieval_tagger import HardwareRetrievalTagger
from services.hardware_retrieval_tagger_runtime import (
    AGENT_ID,
    HardwareRetrievalTaggerRuntimeInvoker,
    build_hardware_retrieval_tagger_runtime,
)
from tools.openai_mock.server import create_server


ROOT = Path(__file__).resolve().parents[1]
SECRET = "HW_RETRIEVAL_RUNTIME_SECRET_MUST_NOT_PERSIST"


@contextmanager
def running_server() -> Iterator[tuple[str, int]]:
    server = create_server("127.0.0.1", 0)
    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.01},
        daemon=True,
    )
    thread.start()
    try:
        yield server.server_address
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def configure(host: str, port: int, payload: dict) -> None:
    raw = json.dumps(
        {"scenario_key": "default", "payload": payload, "behavior": {}},
        ensure_ascii=False,
    ).encode()
    request = Request(
        f"http://{host}:{port}/__mock__/scenario",
        data=raw,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=2) as response:
        assert response.status == 200


def counters(host: str, port: int) -> dict[str, int]:
    with urlopen(f"http://{host}:{port}/__mock__/counters", timeout=2) as response:
        return json.loads(response.read().decode())["data"]


def projection() -> dict:
    return {
        "projection_schema_version": 1,
        "knowledge_id": "KO-HW-W1B-001",
        "public_ref": "HW-PUBLIC-W1B-R1",
        "business_case_id": "A9001",
        "title": "MCU intermittent reset",
        "symptom": "MCU 偶发复位，重新上电恢复",
        "occurrence_condition": "低温启动后偶发",
        "failure_mode": "reset",
        "root_cause": "RESET_N line susceptible to disturbance",
        "failure_mechanism": "noise coupled into reset path",
        "analysis_process": "scope captured RESET_N disturbance",
        "actions": "improve reset filtering",
        "verification_result": "retest passed",
        "engineering_rule": "reset path requires noise margin",
        "design_constraint": "keep reset trace away from aggressors",
        "diagnostic_clue": "power cycle restores operation",
        "verification_method": "cold boot repeated test",
        "applicability": "MCU control board",
        "conclusion": "reset path robustness issue",
        "interface": "CAN",
        "signal": "RESET_N",
        "key_parameters": [{"name": "VDD", "value": "3.3 V", "unit": "V"}],
        "device_refs": [
            {
                "category": "MCU",
                "generic_name_or_series": "STM32 MCU",
                "internal_material_no": None,
                "manufacturer": None,
                "manufacturer_part_no": None,
                "evidence_refs": ["EV-1"],
                "status": "EXPLICIT",
            }
        ],
        "evidence_refs": ["EV-1"],
        "source_domain": "HARDWARE_CASE",
        "source_object_type": "HARDWARE_CASE",
        "formal_revision": 1,
        "formal_status": "ACTIVE",
        "formal_object_hash": "b" * 64,
        "projected_at": "2026-10-07T00:00:00+00:00",
    }


def provider_payload() -> dict:
    return {
        "tags": [
            {
                "term": "偶发复位",
                "kind": "FACT",
                "source_term": "偶发复位",
                "source_fields": ["symptom"],
            },
            {
                "term": "reset-n",
                "kind": "NORMALIZED",
                "source_term": "RESET_N",
                "source_fields": ["signal"],
            },
            {
                "term": "reboot",
                "kind": "EXPANSION",
                "source_term": "偶发复位",
                "source_fields": ["symptom"],
            },
        ]
    }


def model_config(tmp_path: Path, host: str, port: int) -> Path:
    path = tmp_path / "model.local.yaml"
    path.write_text(
        f"""
active_model: hardware_retrieval_test
models:
  hardware_retrieval_test:
    provider: openai_compatible
    base_url: http://{host}:{port}/v1
    api_key: {SECRET}
    model: qwen-hardware-retrieval-test
    temperature: 0
    max_tokens: 4096
""".strip(),
        encoding="utf-8",
    )
    return path


def test_runtime_factory_is_explicit():
    assert (
        build_hardware_retrieval_tagger_runtime
        .__hardware_retrieval_tagger_runtime_factory__
        is True
    )


def test_w1b_reuses_unified_runtime_and_validator(tmp_path: Path):
    with running_server() as (host, port):
        configure(host, port, provider_payload())
        cfg = model_config(tmp_path, host, port)
        runtime_db = tmp_path / "runtime.db"
        invoker = HardwareRetrievalTaggerRuntimeInvoker(
            root=ROOT,
            environ={
                "HARDWARE_CASE_MODEL_CONFIG": str(cfg),
                "HARDWARE_RETRIEVAL_RUNTIME_DB": str(runtime_db),
            },
        )

        assert invoker.resolved.definition.agent_id == AGENT_ID
        assert invoker.resolved.provider.type == "openai_compatible"
        assert invoker.resolved.provider.model == "qwen-hardware-retrieval-test"
        assert SECRET not in invoker.resolved.model_dump_json()

        result = HardwareRetrievalTagger(invoker).tag(projection())

        assert result["contract_version"] == "hardware-retrieval-metadata/v1"
        assert result["tag_counts"] == {
            "FACT": 1,
            "NORMALIZED": 1,
            "EXPANSION": 1,
        }
        expansion = [item for item in result["tags"] if item["kind"] == "EXPANSION"][0]
        assert expansion["usage"] == "RECALL_ONLY"
        assert expansion["derived"] is True
        assert expansion["claim_safe"] is False
        assert counters(host, port)["default"] == 1
        assert SECRET.encode() not in runtime_db.read_bytes()


def test_w1b_request_is_idempotent_for_same_tagger_input(tmp_path: Path):
    with running_server() as (host, port):
        configure(host, port, provider_payload())
        cfg = model_config(tmp_path, host, port)
        invoker = HardwareRetrievalTaggerRuntimeInvoker(
            root=ROOT,
            environ={
                "HARDWARE_CASE_MODEL_CONFIG": str(cfg),
                "HARDWARE_RETRIEVAL_RUNTIME_DB": str(tmp_path / "runtime.db"),
            },
        )
        tagger = HardwareRetrievalTagger(invoker)

        first = tagger.tag(projection())
        second = tagger.tag(projection())

        assert first == second
        assert counters(host, port)["default"] == 1


def test_w1b_runtime_schema_rejects_invalid_tag_kind(tmp_path: Path):
    with running_server() as (host, port):
        configure(
            host,
            port,
            {
                "tags": [
                    {
                        "term": "invented",
                        "kind": "INFERRED_FACT",
                        "source_term": "偶发复位",
                        "source_fields": ["symptom"],
                    }
                ]
            },
        )
        cfg = model_config(tmp_path, host, port)
        invoker = HardwareRetrievalTaggerRuntimeInvoker(
            root=ROOT,
            environ={
                "HARDWARE_CASE_MODEL_CONFIG": str(cfg),
                "HARDWARE_RETRIEVAL_RUNTIME_DB": str(tmp_path / "runtime.db"),
            },
        )

        try:
            HardwareRetrievalTagger(invoker).tag(projection())
        except Exception as error:
            code = str(getattr(error, "code", "") or error)
            assert code
        else:
            raise AssertionError("invalid provider output must fail closed")
