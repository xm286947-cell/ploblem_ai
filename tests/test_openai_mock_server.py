from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from http.client import HTTPConnection
from typing import Iterator

import pytest

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


def request(host: str, port: int, method: str, path: str, body=None, headers=None):
    conn = HTTPConnection(host, port, timeout=2)
    raw = None if body is None else json.dumps(body).encode()
    all_headers = dict(headers or {})
    if raw is not None:
        all_headers.setdefault("Content-Type", "application/json")
        all_headers.setdefault("Content-Length", str(len(raw)))
    conn.request(method, path, body=raw, headers=all_headers)
    response = conn.getresponse()
    data = response.read()
    result_headers = dict(response.getheaders())
    conn.close()
    return response.status, result_headers, data


def auth(extra=None):
    return {"Authorization": "Bearer super-secret-test-key", **(extra or {})}


def configure(host, port, *, key="default", payload="hello", behavior=None):
    status, _, data = request(
        host,
        port,
        "POST",
        "/__mock__/scenario",
        {"scenario_key": key, "payload": payload, "behavior": behavior or {}},
    )
    assert status == 200, data


def test_responses_returns_configured_payload_without_custom_v1_body_fields():
    with running_server() as (host, port):
        payload = {"answer": "ok", "items": [1, 2]}
        configure(host, port, payload=payload)
        status, headers, raw = request(
            host,
            port,
            "POST",
            "/v1/responses",
            {"model": "gpt-test", "input": "hello"},
            auth(),
        )
        body = json.loads(raw)
        assert status == 200
        assert headers["Content-Type"].startswith("application/json")
        assert body["object"] == "response"
        assert body["model"] == "gpt-test"
        assert body["output"][0]["content"][0]["text"] == '{"answer":"ok","items":[1,2]}'


def test_chat_completions_returns_payload():
    with running_server() as (host, port):
        configure(host, port, payload='{"partial":')
        status, _, raw = request(
            host,
            port,
            "POST",
            "/v1/chat/completions",
            {
                "model": "gpt-test",
                "messages": [{"role": "user", "content": "hi"}],
            },
            auth(),
        )
        body = json.loads(raw)
        assert status == 200
        assert body["object"] == "chat.completion"
        assert body["choices"][0]["message"]["content"] == '{"partial":'


def test_fail_first_n_then_success_and_counter_is_exact():
    with running_server() as (host, port):
        configure(
            host,
            port,
            key="retry-case",
            payload="recovered",
            behavior={
                "fail_first_n": 2,
                "fail_status": 429,
                "retry_after": "0",
            },
        )
        headers = auth({"X-Mock-Scenario-Key": "retry-case"})
        statuses = []
        for _ in range(3):
            status, _, _ = request(
                host,
                port,
                "POST",
                "/v1/responses",
                {"model": "gpt-test", "input": "x"},
                headers,
            )
            statuses.append(status)
        assert statuses == [429, 429, 200]

        status, _, raw = request(host, port, "GET", "/__mock__/counters")
        assert status == 200
        assert json.loads(raw)["data"]["retry-case"] == 3


def test_request_history_redacts_secret():
    with running_server() as (host, port):
        configure(host, port, key="secret")
        secret = "sk-this-must-never-be-persisted"
        request(
            host,
            port,
            "POST",
            "/v1/responses",
            {"model": "gpt-test", "input": "x"},
            {
                "Authorization": f"Bearer {secret}",
                "X-Mock-Scenario-Key": "secret",
            },
        )
        _, _, raw = request(
            host,
            port,
            "GET",
            "/__mock__/requests?scenario_key=secret",
        )
        text = raw.decode()
        assert secret not in text
        row = json.loads(text)["data"][0]
        assert row["authorization"] == {"present": True, "scheme": "Bearer"}
        assert row["headers"]["Authorization"] == "[REDACTED]"


def test_models_is_openai_shaped():
    with running_server() as (host, port):
        status, _, raw = request(
            host,
            port,
            "GET",
            "/v1/models",
            headers=auth(),
        )
        body = json.loads(raw)
        assert status == 200
        assert body["object"] == "list"
        assert body["data"][0]["object"] == "model"
        assert body["data"][0]["id"] == "mock-gpt"


def test_missing_auth_is_openai_shaped_401():
    with running_server() as (host, port):
        status, _, raw = request(
            host,
            port,
            "POST",
            "/v1/responses",
            {"model": "gpt-test", "input": "x"},
        )
        body = json.loads(raw)
        assert status == 401
        assert body["error"]["code"] == "invalid_api_key"


def test_responses_stream_sse_contains_deltas_and_completed_event():
    with running_server() as (host, port):
        configure(host, port, payload="abcdefgh", behavior={"chunk_size": 3})
        status, headers, raw = request(
            host,
            port,
            "POST",
            "/v1/responses",
            {"model": "gpt-test", "input": "x", "stream": True},
            auth(),
        )
        text = raw.decode()
        assert status == 200
        assert headers["Content-Type"].startswith("text/event-stream")
        assert "event: response.output_text.delta" in text
        assert '"delta":"abc"' in text
        assert "event: response.completed" in text


def test_chat_stream_uses_data_frames_and_done():
    with running_server() as (host, port):
        configure(host, port, payload="abcdef", behavior={"chunk_size": 2})
        status, headers, raw = request(
            host,
            port,
            "POST",
            "/v1/chat/completions",
            {"model": "gpt-test", "messages": [], "stream": True},
            auth(),
        )
        text = raw.decode()
        assert status == 200
        assert headers["Content-Type"].startswith("text/event-stream")
        assert '"object":"chat.completion.chunk"' in text
        assert "data: [DONE]" in text


def test_delay_is_deterministic_enough_for_timeout_testing():
    with running_server() as (host, port):
        configure(host, port, payload="late", behavior={"delay_ms": 120})
        started = time.monotonic()
        status, _, _ = request(
            host,
            port,
            "POST",
            "/v1/responses",
            {"model": "gpt-test", "input": "x"},
            auth(),
        )
        elapsed = time.monotonic() - started
        assert status == 200
        assert elapsed >= 0.10


def test_truncate_at_produces_invalid_json_body():
    with running_server() as (host, port):
        configure(host, port, payload="hello", behavior={"truncate_at": 20})
        status, _, raw = request(
            host,
            port,
            "POST",
            "/v1/responses",
            {"model": "gpt-test", "input": "x"},
            auth(),
        )
        assert status == 200
        with pytest.raises(json.JSONDecodeError):
            json.loads(raw)


def test_reset_specific_scenario_clears_counter():
    with running_server() as (host, port):
        configure(host, port, key="a", payload="A")
        request(
            host,
            port,
            "POST",
            "/v1/responses",
            {"model": "gpt-test", "input": "x"},
            auth({"X-Mock-Scenario-Key": "a"}),
        )
        status, _, _ = request(
            host,
            port,
            "POST",
            "/__mock__/reset",
            {"scenario_key": "a"},
        )
        assert status == 200
        _, _, raw = request(host, port, "GET", "/__mock__/counters")
        assert "a" not in json.loads(raw)["data"]


def test_raw_response_body_allows_protocol_level_invalid_payload():
    with running_server() as (host, port):
        configure(
            host,
            port,
            payload="ignored",
            behavior={"raw_response_body": "not-json-at-all"},
        )
        status, _, raw = request(
            host,
            port,
            "POST",
            "/v1/responses",
            {"model": "gpt-test", "input": "x"},
            auth(),
        )
        assert status == 200
        assert raw == b"not-json-at-all"


def test_stream_disconnect_stops_before_completed_event():
    with running_server() as (host, port):
        configure(
            host,
            port,
            payload="a" * 200,
            behavior={"chunk_size": 10, "disconnect_at": 180},
        )
        status, _, raw = request(
            host,
            port,
            "POST",
            "/v1/responses",
            {"model": "gpt-test", "input": "x", "stream": True},
            auth(),
        )
        assert status == 200
        text = raw.decode(errors="replace")
        assert "response.completed" not in text
