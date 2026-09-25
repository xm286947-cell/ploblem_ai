from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.request import Request, urlopen

import pytest

from tools.openai_mock.server import Behavior, MockState, Scenario, create_server
from storage_life import ai, runtime_bridge


@contextmanager
def _running_mock() -> Iterator[tuple[str, int]]:
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


def _configure(host: str, port: int, payload: str) -> None:
    raw = json.dumps(
        {
            "scenario_key": "default",
            "payload": payload,
            "behavior": {},
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = Request(
        f"http://{host}:{port}/__mock__/scenario",
        data=raw,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=2) as response:
        assert response.status == 200


def test_real_runtime_handoff_to_storage_secondary_extraction(tmp_path: Path, monkeypatch) -> None:
    project_root = Path(__file__).resolve().parent.parent
    runtime_root = project_root / "vendor" / "unified_agent_runtime"

    with _running_mock() as (host, port):
        model_config = tmp_path / "model.yaml"
        model_config.write_text(
            f"""
active_model: qwen_prod
models:
  qwen_prod:
    provider: openai_compatible
    base_url: http://{host}:{port}/v1
    api_key_env: STORAGE_AGENT_API_KEY
    model: mock-gpt
    temperature: 0
    max_tokens: 8192
    metadata:
      capability_source: CONFIGURED
      capabilities:
        structured_output: unsupported
        token_limit_parameter: max_tokens
""".strip(),
            encoding="utf-8",
        )

        monkeypatch.setenv("UNIFIED_AGENT_RUNTIME_ROOT", str(runtime_root))
        monkeypatch.setenv("STORAGE_LIFE_EXECUTION_MODE", "runtime")
        monkeypatch.setenv("STORAGE_MODEL_CONFIG", str(model_config))
        monkeypatch.setenv("STORAGE_AGENT_API_KEY", "mock-secret")
        monkeypatch.setenv("STORAGE_LIFE_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
        monkeypatch.delenv("RUNTIME_PROVIDER_TRACE", raising=False)
        monkeypatch.delenv("RUNTIME_PROVIDER_DIAGNOSTICS", raising=False)
        runtime_bridge._RUNTIME = None

        useful = (
            "vendor=GigaDevice; model=GD25Q64E; "
            "endurance=100000 cycles; response malformed"
        )
        _configure(host, port, useful)
        schema = {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
        }

        with pytest.raises(runtime_bridge.RuntimeBridgeCallError) as captured:
            runtime_bridge.call_json("Return strict JSON.", {"source": "mock"}, schema)
        exc = captured.value
        assert exc.code == "SEMANTIC_REPAIR_REQUIRED"

        handoff = runtime_bridge.semantic_repair_handoff(exc)
        assert handoff is not None
        assert handoff["recoverable_content_available"] is True
        assert str(handoff["content_ref"]).startswith("semantic-handoff:")
        assert runtime_bridge.resolve_semantic_repair_content(exc) == useful

        # Reconfigure the same OpenAI Mock for the Storage-owned second extraction.
        _configure(host, port, '{"ok":true}')
        result, meta = ai._secondary_extraction_from_semantic_handoff(
            exc,
            device_type="NOR",
            vendor="GigaDevice",
            product_family="GD25Q64E",
            schema=schema,
            field_map={"ok": "ok"},
        )
        assert result == {"ok": True}
        assert meta["status"] == "completed"

        runtime, *_ = runtime_bridge._get_runtime()
        assert (
            runtime.read_semantic_handoff_content(
                task_id="task-not-authorized",
                content_ref=handoff["content_ref"],
            )
            is None
        )

    runtime_bridge._RUNTIME = None


class _SequenceMockState(MockState):
    def __init__(self, payloads):
        super().__init__()
        self._payloads = list(payloads)
        self._last_call_no = 0

    def register_call(self, key, record):
        call_no = super().register_call(key, record)
        self._last_call_no = call_no
        return call_no

    def scenario(self, key):
        if not self._payloads:
            return Scenario(key=key)
        index = min(max(self._last_call_no - 1, 0), len(self._payloads) - 1)
        return Scenario(
            key=key,
            payload=self._payloads[index],
            behavior=Behavior(require_auth=True),
        )


@contextmanager
def _running_sequence_mock(payloads) -> Iterator[tuple[str, int]]:
    server = create_server("127.0.0.1", 0, state=_SequenceMockState(payloads))
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


def test_real_runtime_openai_mock_identify_semantic_handoff_auto_secondary(tmp_path: Path, monkeypatch) -> None:
    project_root = Path(__file__).resolve().parent.parent
    runtime_root = project_root / "vendor" / "unified_agent_runtime"
    first = "vendor=GigaDevice; model=GD25Q64E; device_type=NOR Flash; response malformed"
    second = json.dumps(
        {
            "vendor": {
                "value": "GigaDevice",
                "page": 1,
                "quote": "GigaDevice Semiconductor Inc.",
                "confidence": 0.99,
            },
            "model": {
                "value": "GD25Q64E",
                "page": 1,
                "quote": "GD25Q64E 64M-bit Serial Flash",
                "confidence": 0.99,
            },
            "device_type": {
                "value": "NOR Flash",
                "page": 1,
                "quote": "Serial Flash",
                "confidence": 0.99,
            },
        },
        ensure_ascii=False,
    )

    with _running_sequence_mock([first, second]) as (host, port):
        model_config = tmp_path / "model.yaml"
        model_config.write_text(
            f"""
active_model: qwen_prod
models:
  qwen_prod:
    provider: openai_compatible
    base_url: http://{host}:{port}/v1
    api_key_env: STORAGE_AGENT_API_KEY
    model: mock-gpt
    temperature: 0
    max_tokens: 8192
    metadata:
      capability_source: CONFIGURED
      capabilities:
        structured_output: unsupported
        token_limit_parameter: max_tokens
""".strip(),
            encoding="utf-8",
        )
        monkeypatch.setenv("UNIFIED_AGENT_RUNTIME_ROOT", str(runtime_root))
        monkeypatch.setenv("STORAGE_LIFE_EXECUTION_MODE", "runtime")
        monkeypatch.setenv("STORAGE_MODEL_CONFIG", str(model_config))
        monkeypatch.setenv("STORAGE_AGENT_API_KEY", "mock-secret")
        monkeypatch.setenv("STORAGE_LIFE_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
        monkeypatch.delenv("RUNTIME_PROVIDER_TRACE", raising=False)
        monkeypatch.delenv("RUNTIME_PROVIDER_DIAGNOSTICS", raising=False)
        runtime_bridge._RUNTIME = None

        pages = [(
            1,
            "GigaDevice Semiconductor Inc.\nGD25Q64E 64M-bit Serial Flash\nSerial Flash\n",
            "markdown_text",
        )]
        result = ai.identify_device(pages)
        assert result["vendor"]["value"] == "GigaDevice"
        assert result["model"]["value"] == "GD25Q64E"
        assert result["device_type"]["value"] == "NOR Flash"

        executions = runtime_bridge.last_executions()
        recent = executions[-4:]
        failed = [x for x in recent if (x.get("error") or {}).get("code") == "SEMANTIC_REPAIR_REQUIRED"]
        completed = [x for x in recent if x.get("status") == "COMPLETED"]
        assert failed
        assert completed

    runtime_bridge._RUNTIME = None


def test_real_runtime_openai_mock_secondary_semantic_damage_partitioned_rescue(tmp_path: Path, monkeypatch) -> None:
    """Reproduce the field symptom: primary and normal secondary both return semantic damage.

    The bounded small-field rescue must use the original Runtime handoff text, return only
    strict JSON groups, merge deterministically, and close critical NOR coverage without
    any Storage-side malformed-JSON parsing.
    """
    project_root = Path(__file__).resolve().parent.parent
    runtime_root = project_root / "vendor" / "unified_agent_runtime"

    pages = [(
        1,
        "GigaDevice Semiconductor Inc.\n"
        "GD25Q64E 64M-bit Serial Flash\n"
        "FEATURES\n"
        "Minimum 100,000 Program/Erase Cycles\n"
        "20-year data retention typical\n"
        "VALID PART NUMBERS\n"
        "GD25Q64EBIG\n",
        "markdown_text",
    )]
    primary_invalid = (
        'manufacturer=GigaDevice Semiconductor Inc.; product_family=GD25Q64E; '
        'pe_cycles=100000 cycles page=1 quote="Minimum 100,000 Program/Erase Cycles"; '
        'retention=20 years page=1 quote="20-year data retention typical"; malformed json'
    )
    secondary_invalid = "second extraction still useful but not strict json"

    def item(key: str):
        found = {
            "manufacturer": {
                "value": "GigaDevice Semiconductor Inc.",
                "quote": "GigaDevice Semiconductor Inc.",
            },
            "product_family": {
                "value": "GD25Q64E",
                "quote": "GD25Q64E 64M-bit Serial Flash",
            },
            "covered_part_numbers": {
                "value": "GD25Q64EBIG",
                "quote": "GD25Q64EBIG",
                "scope_values": ["GD25Q64EBIG"],
            },
            "pe_cycles": {
                "value": "100000",
                "unit": "cycles",
                "quote": "Minimum 100,000 Program/Erase Cycles",
            },
            "retention": {
                "value": "20",
                "unit": "years",
                "quote": "20-year data retention typical",
            },
        }.get(key)
        if found is None:
            return {
                "field_key": key,
                "value": None,
                "unit": None,
                "condition": None,
                "scope_type": "product_family",
                "scope_values": [],
                "evidence": None,
                "conflict_evidence": [],
                "confidence": 0.0,
                "status": "ambiguous",
                "derived": False,
                "knowledge_type": "specification",
            }
        return {
            "field_key": key,
            "value": found["value"],
            "unit": found.get("unit"),
            "condition": None,
            "scope_type": "part_number" if found.get("scope_values") else "product_family",
            "scope_values": found.get("scope_values", []),
            "evidence": {
                "source_id": "pdf",
                "page": 1,
                "section": "FEATURES",
                "quote": found["quote"],
            },
            "conflict_evidence": [],
            "confidence": 0.99,
            "status": "found",
            "derived": False,
            "knowledge_type": "specification",
        }

    groups = ai._secondary_partition_groups("NOR Flash")
    group_payloads = [
        json.dumps({"fields": [item(key) for key in group]}, ensure_ascii=False)
        for group in groups
    ]
    # validation_attempts=2: primary damage twice, normal secondary damage twice,
    # then every small-field partition succeeds on its first Provider call.
    sequence = [primary_invalid, primary_invalid, secondary_invalid, secondary_invalid, *group_payloads]

    with _running_sequence_mock(sequence) as (host, port):
        model_config = tmp_path / "model.yaml"
        model_config.write_text(
            f"""
active_model: qwen_prod
models:
  qwen_prod:
    provider: openai_compatible
    base_url: http://{host}:{port}/v1
    api_key_env: STORAGE_AGENT_API_KEY
    model: mock-gpt
    temperature: 0
    max_tokens: 8192
    metadata:
      capability_source: CONFIGURED
      capabilities:
        structured_output: unsupported
        token_limit_parameter: max_tokens
""".strip(),
            encoding="utf-8",
        )
        monkeypatch.setenv("UNIFIED_AGENT_RUNTIME_ROOT", str(runtime_root))
        monkeypatch.setenv("STORAGE_LIFE_EXECUTION_MODE", "runtime")
        monkeypatch.setenv("STORAGE_MODEL_CONFIG", str(model_config))
        monkeypatch.setenv("STORAGE_AGENT_API_KEY", "mock-secret")
        monkeypatch.setenv("STORAGE_LIFE_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
        monkeypatch.delenv("RUNTIME_PROVIDER_TRACE", raising=False)
        monkeypatch.delenv("RUNTIME_PROVIDER_DIAGNOSTICS", raising=False)
        runtime_bridge._RUNTIME = None

        result = ai.extract_specification_bundle_once(
            [{"source_id": "pdf", "pages": pages}],
            "NOR Flash",
            "GigaDevice",
            "GD25Q64E",
        )
        assert result["secondary_extraction"]["status"] == "completed_partitioned"
        assert result["secondary_extraction"]["successful_groups"] == len(groups)
        assert result["secondary_extraction"]["failed_groups"] == []
        assert result["coverage"]["critical_unresolved"] == []
        states = {x["field_key"]: x["state"] for x in result["coverage"]["states"]}
        assert states["pe_cycles"] == "FOUND"
        assert states["retention"] == "FOUND"
        assert result["model_calls"] == 2 + len(groups)

        executions = runtime_bridge.last_executions()
        semantic_failures = [
            row for row in executions
            if (row.get("error") or {}).get("code") == "SEMANTIC_REPAIR_REQUIRED"
        ]
        assert len(semantic_failures) >= 2
        assert all("semantic_handoff" in row["error"] for row in semantic_failures)

    runtime_bridge._RUNTIME = None
