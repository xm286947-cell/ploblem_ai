from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.request import Request, urlopen

from services.hardware_case_r1_runtime import (
    R1_AGENT_ID,
    R1_EXTRACTION_CONTRACT_VERSION,
    HardwareCaseR1RuntimeStructurer,
    build_hardware_case_r1_structurer,
)
from tools.openai_mock.server import create_server


ROOT = Path(__file__).resolve().parents[1]
SECRET = "HC_R1_V2_SECRET_MUST_NOT_PERSIST"


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


def candidate(value=None, refs=None, status=None):
    refs = list(refs or [])
    if status is None:
        status = "EXTRACTED" if value is not None else "MISSING"
    return {
        "value": value,
        "extraction_status": status,
        "evidence_block_ids": refs,
        "confidence": 0.9 if status == "EXTRACTED" else None,
        "warnings": [],
    }


def reusable(value=None, refs=None, derived=None, status=None):
    return {
        **candidate(value, refs, status),
        "derived_from_fields": list(derived or []),
        "review_status": "UNREVIEWED",
    }


def payload() -> dict:
    facts = {
        name: candidate()
        for name in (
            "background", "symptom", "impact", "occurrence_condition",
            "analysis_process", "failure_mode", "root_cause",
            "failure_mechanism", "actions", "verification_result", "conclusion",
        )
    }
    facts["symptom"] = candidate("synthetic symptom", ["B1"])
    facts["root_cause"] = candidate("synthetic root cause", ["B2"])
    facts["actions"] = candidate("synthetic action", ["B3"])
    return {
        "contract_version": R1_EXTRACTION_CONTRACT_VERSION,
        "engineering_context": {
            "primary_subject": candidate("MCU", ["B1"]),
            "component_or_device": candidate("MCU", ["B1"]),
            "interface": candidate("UART", ["B1"]),
            "signal": candidate(),
            "peer_device_or_load": candidate(),
            "key_parameters": [],
        },
        "facts": facts,
        "conflicts": [],
        "reusable_knowledge_candidate": {
            "engineering_rule": reusable(),
            "design_constraint": reusable(),
            "diagnostic_clue": reusable(),
            "verification_method": reusable(),
            "applicability": reusable(),
            "conclusion": reusable(),
        },
    }


def document() -> dict:
    return {
        "input_contract": "hardware-case-r1-agent-input/v2",
        "source_fact": {
            "source_id": "synthetic-source",
            "business_case_id": "A0000",
            "raw_title": "synthetic",
        },
        "markdown": "synthetic",
        "evidence_blocks": [
            {"block_id": "B1", "text": "synthetic symptom"},
            {"block_id": "B2", "text": "synthetic root cause"},
            {"block_id": "B3", "text": "synthetic action"},
        ],
    }


def test_r1_runtime_factory_is_explicit():
    assert build_hardware_case_r1_structurer.__hardware_case_r1_structurer_factory__ is True


def test_r1_v2_uses_shared_unified_runtime_and_returns_provenance(tmp_path: Path):
    with running_server() as (host, port):
        configure(host, port, payload())
        model_config = tmp_path / "model.local.yaml"
        model_config.write_text(
            f"""
active_model: hardware_case_r1_test
models:
  hardware_case_r1_test:
    provider: openai_compatible
    base_url: http://{host}:{port}/v1
    api_key: {SECRET}
    model: qwen-hardware-r1-test
    temperature: 0
    max_tokens: 8192
""".strip(),
            encoding="utf-8",
        )
        runtime_db = tmp_path / "runtime.db"
        structurer = HardwareCaseR1RuntimeStructurer(
            root=ROOT,
            environ={
                "HARDWARE_CASE_MODEL_CONFIG": str(model_config),
                "HARDWARE_CASE_RUNTIME_DB": str(runtime_db),
            },
        )

        assert structurer.resolved.definition.agent_id == R1_AGENT_ID
        assert structurer.resolved.definition.version == "v2"
        assert SECRET not in structurer.resolved.model_dump_json()

        result = structurer(document())

        assert result["contract_version"] == R1_EXTRACTION_CONTRACT_VERSION
        assert result["engineering_context"]["primary_subject"]["value"] == "MCU"
        assert result["__runtime_meta__"]["agent_id"] == R1_AGENT_ID
        assert result["__runtime_meta__"]["agent_config_version"] == "v2"
        assert result["__runtime_meta__"]["run_id"].startswith("run-")
        assert counters(host, port)["default"] == 1
        assert SECRET.encode() not in runtime_db.read_bytes()


def test_r1_v2_request_is_idempotent(tmp_path: Path):
    with running_server() as (host, port):
        configure(host, port, payload())
        model_config = tmp_path / "model.local.yaml"
        model_config.write_text(
            f"""
active_model: hardware_case_r1_test
models:
  hardware_case_r1_test:
    provider: openai_compatible
    base_url: http://{host}:{port}/v1
    api_key: HC_R1_IDEMPOTENT_SECRET
    model: qwen-hardware-r1-test
    temperature: 0
    max_tokens: 8192
""".strip(),
            encoding="utf-8",
        )
        structurer = HardwareCaseR1RuntimeStructurer(
            root=ROOT,
            environ={
                "HARDWARE_CASE_MODEL_CONFIG": str(model_config),
                "HARDWARE_CASE_RUNTIME_DB": str(tmp_path / "runtime.db"),
            },
        )

        first = structurer(document())
        second = structurer(document())

        assert first == second
        assert counters(host, port)["default"] == 1
