from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest

from runtime import (
    AgentRequest,
    ErrorCategory,
    ExecutionPolicy,
    LightweightExecutionEngine,
    RetryBudget,
    RetryPolicy,
    RuntimeStatus,
    RuntimeStepError,
    SimulatedCrash,
    SqliteTaskStore,
)
from storage_e2e import JsonTruncationAwareStorageAdapter
from tools.openai_mock.server import create_server


SECRET = "STORAGE_E2E_TEST_SECRET"
GOLDEN = [{
    "field_id": "pe_cycle",
    "status": "FOUND",
    "normalized_value": 3000,
    "unit": "cycles",
    "evidence": [],
    "review_reason": "storage-openai-mock-e2e",
}]


@contextmanager
def running_server() -> Iterator[tuple[str, int]]:
    server = create_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield server.server_address
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def configure(host: str, port: int, *, payload: Any, behavior: dict[str, Any] | None = None) -> None:
    configure_key(host, port, key="default", payload=payload, behavior=behavior)


def configure_key(host: str, port: int, *, key: str, payload: Any, behavior: dict[str, Any] | None = None) -> None:
    body = json.dumps({"scenario_key": key, "payload": payload, "behavior": behavior or {}}).encode()
    with urlopen(Request(f"http://{host}:{port}/__mock__/scenario", data=body, method="POST", headers={"Content-Type": "application/json"}), timeout=2) as response:
        assert response.status == 200


def counters(host: str, port: int) -> dict[str, int]:
    with urlopen(f"http://{host}:{port}/__mock__/counters", timeout=2) as response:
        return json.loads(response.read().decode())["data"]


def policy(budget: int, *, validation: int = 1, transport: int = 1) -> ExecutionPolicy:
    return ExecutionPolicy(
        validation_retry=RetryPolicy(max_attempts=validation),
        transport_retry=RetryPolicy(max_attempts=transport),
        step_retry=RetryPolicy(max_attempts=1),
        retry_budget=RetryBudget(
            max_provider_calls_per_step=budget,
            max_step_attempts=1,
            max_validation_cycles_per_step_attempt=validation,
            max_transport_attempts_per_model_call=transport,
        ),
        model_policy={"runtime_retry_owner": True, "sdk_retry": 0},
    )


def storage_provider(host: str, port: int, *, scenario_for_call=None):
    def call(payload: dict[str, Any], context: dict[str, Any]):
        seq = int(context["runtime"]["provider_call_seq"])
        key = scenario_for_call(seq) if scenario_for_call else "default"
        request = Request(
            f"http://{host}:{port}/v1/chat/completions",
            data=json.dumps({"model": "mock-gpt", "messages": [{"role": "user", "content": json.dumps(payload)}]}).encode(),
            method="POST",
            headers={"Authorization": "Bearer mock-key", "Content-Type": "application/json", "X-Mock-Scenario-Key": key},
        )
        try:
            with urlopen(request, timeout=1) as response:
                raw = json.loads(response.read().decode())
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise RuntimeStepError(str(exc), code="PROVIDER_TRANSPORT", category=ErrorCategory.TRANSPORT, retryable=True) from exc
        return type("Response", (), {"text": raw["choices"][0]["message"]["content"], "finish_reason": raw["choices"][0].get("finish_reason"), "raw": raw})()

    return call


def run_storage(tmp_path: Path, host: str, port: int, *, request_id: str, budget: int, validation: int = 1, transport: int = 1, scenario_for_call=None):
    store = SqliteTaskStore(tmp_path / f"{request_id}.db")
    engine = LightweightExecutionEngine(store)
    adapter = JsonTruncationAwareStorageAdapter(engine, storage_provider(host, port, scenario_for_call=scenario_for_call))
    adapter._default_policy = lambda max_provider_calls: policy(max_provider_calls, validation=validation, transport=transport)
    return store, adapter.execute({"device_type": "eMMC", "parameter_scope": "lifetime"}, request_id=request_id, golden=GOLDEN, max_provider_calls=budget)


def test_m01_normal_structured_response_schema_golden_and_single_commit(tmp_path):
    with running_server() as (host, port):
        configure(host, port, payload=json.dumps(GOLDEN))
        store, outcome = run_storage(tmp_path, host, port, request_id="storage-m01", budget=1)
        assert outcome.runtime_result.status == RuntimeStatus.COMPLETED
        assert outcome.runtime_result.execution.provider_calls == counters(host, port)["default"] == 1
        assert outcome.golden_report and outcome.golden_report.passed
        assert len(store.list_committed_execution_keys(outcome.runtime_result.task_id)) == 1


def test_m02_429_recovery_runtime_owns_retry_and_counter_reconciles(tmp_path):
    with running_server() as (host, port):
        configure(host, port, payload=json.dumps(GOLDEN), behavior={"fail_first_n": 1, "fail_status": 429, "retry_after": "0"})
        _, outcome = run_storage(tmp_path, host, port, request_id="storage-m02", budget=2, transport=2)
        assert outcome.runtime_result.status == RuntimeStatus.COMPLETED
        assert outcome.runtime_result.execution.provider_calls == counters(host, port)["default"] == 2


def test_m03_persistent_503_exhausts_budget_without_business_commit(tmp_path):
    with running_server() as (host, port):
        configure(host, port, payload=json.dumps(GOLDEN), behavior={"status": 503})
        store, outcome = run_storage(tmp_path, host, port, request_id="storage-m03", budget=2, transport=2)
        assert outcome.runtime_result.status == RuntimeStatus.FAILED
        assert outcome.runtime_result.execution.provider_calls == counters(host, port)["default"] == 2
        assert outcome.runtime_result.execution.retry_budget_exhausted is True
        assert outcome.golden_report is None
        assert store.list_committed_execution_keys(outcome.runtime_result.task_id) == set()


def test_m04_connection_error_is_transport_retry_owned_by_runtime(tmp_path):
    with running_server() as (host, port):
        configure(host, port, payload=json.dumps(GOLDEN), behavior={"disconnect_before_response": True})
        _, outcome = run_storage(tmp_path, host, port, request_id="storage-m04", budget=2, transport=2)
        assert outcome.runtime_result.status == RuntimeStatus.FAILED
        assert outcome.runtime_result.error and outcome.runtime_result.error.category == ErrorCategory.TRANSPORT
        assert outcome.runtime_result.execution.provider_calls == counters(host, port)["default"] == 2


def test_m05_json_truncation_validation_retry_then_golden(tmp_path):
    with running_server() as (host, port):
        configure(host, port, payload='[{"field_id":"pe_cycle","status":"FOUND",')
        configure_key(host, port, key="storage-complete", payload=json.dumps(GOLDEN))
        _, outcome = run_storage(tmp_path, host, port, request_id="storage-m05", budget=2, validation=2, scenario_for_call=lambda seq: "default" if seq == 1 else "storage-complete")
        assert outcome.runtime_result.status == RuntimeStatus.COMPLETED
        assert outcome.runtime_result.execution.provider_calls == 2
        assert outcome.golden_report and outcome.golden_report.passed


def test_m06_invalid_json_never_becomes_business_result(tmp_path):
    with running_server() as (host, port):
        configure(host, port, payload="not-json")
        store, outcome = run_storage(tmp_path, host, port, request_id="storage-m06", budget=2, validation=2)
        assert outcome.runtime_result.status == RuntimeStatus.FAILED
        assert outcome.runtime_result.error and outcome.runtime_result.error.category == ErrorCategory.VALIDATION
        assert outcome.golden_report is None
        assert store.list_committed_execution_keys(outcome.runtime_result.task_id) == set()


def test_m07_secret_safety_preserves_business_fields(tmp_path, monkeypatch):
    from tests.test_openai_mock_runtime_secret_resume import build_runtime, raw_database_dump, request_history
    with running_server() as (host, port):
        monkeypatch.setenv("STORAGE_E2E_TEST_SECRET", SECRET)
        monkeypatch.setenv("PROVIDER_API_KEY", SECRET)
        configure(host, port, payload="secret-safe")
        store = SqliteTaskStore(tmp_path / "storage-m07.db")
        runtime = build_runtime(tmp_path / "storage-m07-app", store, base_url=f"http://{host}:{port}/v1")
        result = runtime.invoke(AgentRequest(request_id="storage-m07", agent_id="mock-secret-agent", input={"password": "business-password", "api_key": "business-api-key", "token": "business-token"}, context={"provider_api_key": SECRET}, metadata={"runtime_secret": SECRET}))
        dump = raw_database_dump(store)
        assert result.status == RuntimeStatus.COMPLETED
        assert SECRET not in dump and SECRET not in json.dumps(request_history(host, port))
        assert "business-password" in dump and "business-api-key" in dump and "business-token" in dump


def test_m08_resume_reresolves_secret_and_commits_once_after_crash(tmp_path, monkeypatch):
    from tests.test_openai_mock_runtime_secret_resume import build_runtime, raw_database_dump
    with running_server() as (host, port):
        configure(host, port, payload="resume-safe")
        monkeypatch.setenv("STORAGE_E2E_TEST_SECRET", SECRET)
        monkeypatch.setenv("PROVIDER_API_KEY", SECRET)
        armed = {"value": True}
        def fault(point: str) -> None:
            if armed["value"] and point == "after_provider_return_before_commit":
                armed["value"] = False
                raise SimulatedCrash(point)
        store = SqliteTaskStore(tmp_path / "storage-m08.db")
        runtime = build_runtime(tmp_path / "storage-m08-app", store, base_url=f"http://{host}:{port}/v1", fault_injector=fault)
        request = AgentRequest(request_id="storage-m08", agent_id="mock-secret-agent", input={"password": "business-password"}, context={"provider_api_key": SECRET}, metadata={"runtime_secret": SECRET})
        with pytest.raises(SimulatedCrash):
            runtime.invoke(request)
        task = store.get_task_by_request_id(request.request_id)
        assert task is not None and counters(host, port)["default"] == 1
        resumed = build_runtime(tmp_path / "storage-m08-resume", store, base_url=f"http://{host}:{port}/v1")
        assert resumed.resume(task.task_id).status == RuntimeStatus.COMPLETED
        assert counters(host, port)["default"] == 2
        assert len(store.list_committed_execution_keys(task.task_id)) == 1
        assert SECRET not in raw_database_dump(store)
