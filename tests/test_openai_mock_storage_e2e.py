from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from typing import Iterator
from urllib.request import Request, urlopen

from runtime import SqliteTaskStore
from runtime.engine import LightweightExecutionEngine
from storage_e2e import JsonTruncationAwareStorageAdapter, ProviderResponse
from tools.openai_mock.server import create_server


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


def configure(host: str, port: int, key: str, payload: str) -> None:
    raw = json.dumps(
        {
            "scenario_key": key,
            "payload": payload,
            "behavior": {},
        }
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


def test_rt_mock_010_and_ac13_storage_runtime_mock_json_truncation_recovery(tmp_path):
    golden = [
        {
            "field_id": "pe_cycle",
            "status": "FOUND",
            "normalized_value": 3000,
            "unit": "cycles",
            "evidence": [],
            "review_reason": "mock-e2e",
        }
    ]

    with running_server() as (host, port):
        configure(
            host,
            port,
            "storage-truncated",
            '[{"field_id":"pe_cycle","status":"FOUND",',
        )
        configure(
            host,
            port,
            "storage-complete",
            json.dumps(golden, ensure_ascii=False),
        )

        store = SqliteTaskStore(tmp_path / "storage-mock-e2e.db")
        runtime = LightweightExecutionEngine(store)

        def provider(_payload, context):
            seq = int(context["runtime"]["provider_call_seq"])
            key = "storage-truncated" if seq == 1 else "storage-complete"
            body = json.dumps(
                {
                    "model": "mock-gpt",
                    "messages": [{"role": "user", "content": "extract storage fields"}],
                }
            ).encode()
            req = Request(
                f"http://{host}:{port}/v1/chat/completions",
                data=body,
                method="POST",
                headers={
                    "Authorization": "Bearer mock-key",
                    "Content-Type": "application/json",
                    "X-Mock-Scenario-Key": key,
                },
            )
            with urlopen(req, timeout=2) as response:
                raw = json.loads(response.read().decode())
            return ProviderResponse(
                text=raw["choices"][0]["message"]["content"],
                finish_reason=raw["choices"][0]["finish_reason"],
                raw=raw,
            )

        adapter = JsonTruncationAwareStorageAdapter(runtime, provider)
        outcome = adapter.execute(
            {"device_type": "eMMC", "parameter_scope": "lifetime"},
            request_id="rt-mock-storage-e2e",
            golden=golden,
            max_provider_calls=2,
        )

        assert outcome.runtime_result.status.value == "COMPLETED"
        assert outcome.runtime_result.execution.provider_calls == 2
        assert outcome.reviewed_specification is not None
        assert outcome.golden_report is not None
        assert outcome.golden_report.passed is True
        assert outcome.golden_report.diffs == []

        data = counters(host, port)
        assert data["storage-truncated"] == 1
        assert data["storage-complete"] == 1
