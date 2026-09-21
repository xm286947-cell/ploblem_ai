from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import threading
from typing import Iterator
from urllib.request import Request, urlopen

import yaml

from runtime import AgentRequest, RuntimeStatus, SqliteTaskStore
from runtime.adapters import StorageFieldResult, StorageGoldenFieldComparator
from runtime.config import AgentConfigLoader, ConfiguredAgentRuntime
from tools.openai_mock.server import create_server


ROOT = Path(__file__).resolve().parents[1]
AGENT_CONFIG = ROOT / "config/runtime/agents/storage.emmc.parameter_extract.yaml"
SECRET = "RCFG01_LOCAL_ONLY_SECRET"

GOLDEN = [
    {
        "field_id": "pe_cycle",
        "status": "FOUND",
        "normalized_value": 3000,
        "unit": "cycles",
        "evidence": [],
        "review_reason": None,
    }
]


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


def _configure_mock(host: str, port: int) -> None:
    payload = [
        {
            "field_id": "pe_cycle",
            "status": "FOUND",
            "normalized_value": 3000,
            "unit": "cycles",
            "evidence": [],
        }
    ]
    body = json.dumps(
        {"scenario_key": "default", "payload": payload, "behavior": {}},
        ensure_ascii=False,
    ).encode("utf-8")
    request = Request(
        f"http://{host}:{port}/__mock__/scenario",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=2) as response:
        assert response.status == 200


def _raw_runtime_bytes(store: SqliteTaskStore) -> bytes:
    chunks: list[bytes] = []
    for path in sorted(store.db_path.parent.glob(store.db_path.name + "*")):
        if path.is_file():
            chunks.append(path.read_bytes())
    return b"".join(chunks)


def _loader(model_config: Path) -> AgentConfigLoader:
    return AgentConfigLoader(
        root=ROOT,
        model_profiles=model_config,
        schemas={"StorageFieldResult": StorageFieldResult},
        content_strategies={
            "storage_linked_fields@1": {
                "version": "1",
                "kind": "storage_linked_fields",
            }
        },
        completeness_gates={
            "storage_parameter_gate": {
                "version": "v1",
                "kind": "storage_parameter_gate",
            }
        },
        environ={},
    )


def test_rcfg01_agent_business_definition_remains_model_ref_qwen_prod() -> None:
    raw = yaml.safe_load(AGENT_CONFIG.read_text(encoding="utf-8"))

    assert raw["agent_id"] == "storage.emmc.parameter_extract"
    assert raw["model_ref"] == "qwen_prod"
    assert "provider" not in raw
    assert "provider_ref" not in raw


def test_rcfg01_user_model_config_runs_same_runtime_and_keeps_secret_out(
    tmp_path: Path,
) -> None:
    with _running_mock() as (host, port):
        _configure_mock(host, port)
        model_config = tmp_path / "model.local.yaml"
        model_config.write_text(
            f"""
active_model: qwen_prod
models:
  qwen_prod:
    provider: openai_compatible
    base_url: http://{host}:{port}/v1
    api_key: {SECRET}
    model: qwen3.8-max
    temperature: 0
    max_tokens: 8192
""".strip(),
            encoding="utf-8",
        )

        loader = _loader(model_config)
        store = SqliteTaskStore(tmp_path / "runtime.db")
        runtime = ConfiguredAgentRuntime(store, config_loader=loader)
        resolved = runtime.load_agent(AGENT_CONFIG)

        assert resolved.provider.profile_ref == "qwen_prod"
        assert resolved.provider.model == "qwen3.8-max"
        assert resolved.provider.base_url == f"http://{host}:{port}/v1"
        assert resolved.provider.base_url_env is None
        assert resolved.provider.api_key_env is None
        assert SECRET not in resolved.model_dump_json()

        result = runtime.invoke(
            AgentRequest(
                request_id="rcfg01-storage-user-config",
                agent_id="storage.emmc.parameter_extract",
                input={
                    "device_type": "eMMC",
                    "parameter_scope": "lifetime",
                    "source_text": (
                        "The eMMC device life specification states "
                        "pe_cycle = 3000 cycles."
                    ),
                    "required_fields": ["pe_cycle"],
                },
            )
        )

        assert result.status == RuntimeStatus.COMPLETED
        assert result.execution.provider_calls == 1

        actual = [StorageFieldResult.model_validate(item) for item in result.data]
        report = StorageGoldenFieldComparator().compare(
            GOLDEN,
            actual,
            accepted_differences={
                "pe_cycle.review_reason": (
                    "Provider wording is outside the smoke-gate contract."
                )
            },
        )
        assert report.passed is True

        snapshot = store.get_execution_snapshot(
            result.execution.execution_snapshot_id
        )
        assert SECRET not in snapshot.model_dump_json()
        assert SECRET not in result.model_dump_json()
        assert SECRET.encode("utf-8") not in _raw_runtime_bytes(store)
