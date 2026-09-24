from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.request import Request, urlopen

from services.hardware_case_runtime_adapter import (
    AGENT_ID,
    HardwareCaseRuntimeStructurer,
    build_hardware_case_structurer,
)
from tools.openai_mock.server import create_server


ROOT = Path(__file__).resolve().parents[1]
SECRET = "HC_RUNTIME_SECRET_MUST_NOT_PERSIST"


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


def payload() -> dict:
    return {
        "title": "Synthetic power issue",
        "product_context": {"board": "synthetic"},
        "facts": {
            "symptom": {
                "value": "Power rail drops during synthetic surge.",
                "evidence_block_ids": ["B1"],
            },
            "root_cause": {
                "value": "Synthetic protection margin is insufficient.",
                "evidence_block_ids": ["B2"],
            },
            "actions": {
                "value": "Increase synthetic protection margin.",
                "evidence_block_ids": ["B3"],
            },
        },
        "circuit_feature_links": [],
        "material_links": [],
    }


def document() -> dict:
    return {
        "file_name": "A1234-synthetic.docx",
        "source_id": "synthetic-source",
        "source_ref": "word:A1234-synthetic.docx",
        "blocks": [
            {
                "block_id": "B1",
                "block_type": "PARAGRAPH",
                "text": "Power rail drops during synthetic surge.",
                "source_locator": {"block_id": "B1"},
            },
            {
                "block_id": "B2",
                "block_type": "PARAGRAPH",
                "text": "Synthetic protection margin is insufficient.",
                "source_locator": {"block_id": "B2"},
            },
            {
                "block_id": "B3",
                "block_type": "PARAGRAPH",
                "text": "Increase synthetic protection margin.",
                "source_locator": {"block_id": "B3"},
            },
        ],
    }


def test_hardware_case_runtime_factory_is_explicit():
    assert build_hardware_case_structurer.__hardware_case_structurer_factory__ is True


def test_hardware_case_runtime_structurer_uses_unified_runtime_and_model_config(
    tmp_path: Path,
) -> None:
    with running_server() as (host, port):
        configure(host, port, payload())
        model_config = tmp_path / "model.local.yaml"
        model_config.write_text(
            f"""
active_model: hardware_case_test
models:
  hardware_case_test:
    provider: openai_compatible
    base_url: http://{host}:{port}/v1
    api_key: {SECRET}
    model: qwen-hardware-case-test
    temperature: 0
    max_tokens: 8192
""".strip(),
            encoding="utf-8",
        )
        runtime_db = tmp_path / "runtime.db"
        structurer = HardwareCaseRuntimeStructurer(
            root=ROOT,
            environ={
                "HARDWARE_CASE_MODEL_CONFIG": str(model_config),
                "HARDWARE_CASE_RUNTIME_DB": str(runtime_db),
            },
        )

        assert structurer.resolved.definition.agent_id == AGENT_ID
        assert structurer.resolved.provider.type == "openai_compatible"
        assert structurer.resolved.provider.model == "qwen-hardware-case-test"
        assert SECRET not in structurer.resolved.model_dump_json()

        result = structurer(document())

        assert result["facts"]["symptom"]["evidence_block_ids"] == ["B1"]
        assert result["facts"]["root_cause"]["evidence_block_ids"] == ["B2"]
        assert result["facts"]["actions"]["evidence_block_ids"] == ["B3"]
        assert counters(host, port)["default"] == 1
        assert SECRET.encode() not in runtime_db.read_bytes()


def test_hardware_case_runtime_request_is_idempotent_for_same_document(
    tmp_path: Path,
) -> None:
    with running_server() as (host, port):
        configure(host, port, payload())
        model_config = tmp_path / "model.local.yaml"
        model_config.write_text(
            f"""
active_model: hardware_case_test
models:
  hardware_case_test:
    provider: openai_compatible
    base_url: http://{host}:{port}/v1
    api_key: HC_RUNTIME_IDEMPOTENT_SECRET
    model: qwen-hardware-case-test
    temperature: 0
    max_tokens: 8192
""".strip(),
            encoding="utf-8",
        )
        structurer = HardwareCaseRuntimeStructurer(
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
