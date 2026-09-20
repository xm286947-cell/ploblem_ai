from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from typing import Iterator
from urllib.request import Request, urlopen

from openai import OpenAI

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
        {"scenario_key": key, "payload": payload, "behavior": {}}
    ).encode()
    request = Request(
        f"http://{host}:{port}/__mock__/scenario",
        data=raw,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=2) as response:
        assert response.status == 200


def test_official_python_sdk_responses_only_needs_base_url_switch():
    with running_server() as (host, port):
        configure(host, port, "sdk-responses", "SDK responses OK")
        client = OpenAI(
            api_key="mock-key",
            base_url=f"http://{host}:{port}/v1",
            max_retries=0,
        )
        response = client.responses.create(
            model="mock-gpt",
            input="hello",
            extra_headers={"X-Mock-Scenario-Key": "sdk-responses"},
        )
        assert response.output_text == "SDK responses OK"


def test_official_python_sdk_chat_completions_only_needs_base_url_switch():
    with running_server() as (host, port):
        configure(host, port, "sdk-chat", "SDK chat OK")
        client = OpenAI(
            api_key="mock-key",
            base_url=f"http://{host}:{port}/v1",
            max_retries=0,
        )
        response = client.chat.completions.create(
            model="mock-gpt",
            messages=[{"role": "user", "content": "hello"}],
            extra_headers={"X-Mock-Scenario-Key": "sdk-chat"},
        )
        assert response.choices[0].message.content == "SDK chat OK"


def test_official_python_sdk_models_list_parses_response():
    with running_server() as (host, port):
        client = OpenAI(
            api_key="mock-key",
            base_url=f"http://{host}:{port}/v1",
            max_retries=0,
        )
        models = client.models.list()
        assert models.data[0].id == "mock-gpt"


def test_official_python_sdk_responses_stream_parses_events():
    with running_server() as (host, port):
        configure(host, port, "sdk-stream", "streaming works")
        client = OpenAI(
            api_key="mock-key",
            base_url=f"http://{host}:{port}/v1",
            max_retries=0,
        )
        stream = client.responses.create(
            model="mock-gpt",
            input="hello",
            stream=True,
            extra_headers={"X-Mock-Scenario-Key": "sdk-stream"},
        )
        deltas = []
        event_types = []
        for event in stream:
            event_types.append(event.type)
            if event.type == "response.output_text.delta":
                deltas.append(event.delta)
        assert "".join(deltas) == "streaming works"
        assert "response.completed" in event_types


def test_official_python_sdk_chat_stream_parses_chunks():
    with running_server() as (host, port):
        configure(host, port, "sdk-chat-stream", "chat stream")
        client = OpenAI(
            api_key="mock-key",
            base_url=f"http://{host}:{port}/v1",
            max_retries=0,
        )
        stream = client.chat.completions.create(
            model="mock-gpt",
            messages=[{"role": "user", "content": "hello"}],
            stream=True,
            extra_headers={"X-Mock-Scenario-Key": "sdk-chat-stream"},
        )
        chunks = []
        for event in stream:
            if event.choices and event.choices[0].delta.content:
                chunks.append(event.choices[0].delta.content)
        assert "".join(chunks) == "chat stream"
