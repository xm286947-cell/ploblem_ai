from __future__ import annotations

import argparse
import json
import socket
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterable
from urllib.parse import parse_qs, urlparse


DEFAULT_MODEL = "mock-gpt"
DEFAULT_SCENARIO_KEY = "default"
MAX_REQUEST_HISTORY = 1000
_SECRET_HEADER_NAMES = {
    "authorization",
    "api-key",
    "x-api-key",
    "openai-organization",
    "openai-project",
}


@dataclass(slots=True)
class Behavior:
    status: int = 200
    delay_ms: int = 0
    stream: bool | None = None
    fail_first_n: int = 0
    fail_status: int = 429
    retry_after: str | None = None
    truncate_at: int | None = None
    disconnect_before_response: bool = False
    disconnect_at: int | None = None
    chunk_size: int = 16
    headers: dict[str, str] = field(default_factory=dict)
    raw_response_body: str | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "Behavior":
        raw = raw or {}
        return cls(
            status=_as_int(raw.get("status", 200), "status", minimum=200, maximum=599),
            delay_ms=_as_int(raw.get("delay_ms", 0), "delay_ms", minimum=0),
            stream=_as_optional_bool(raw.get("stream"), "stream"),
            fail_first_n=_as_int(raw.get("fail_first_n", 0), "fail_first_n", minimum=0),
            fail_status=_as_int(raw.get("fail_status", 429), "fail_status", minimum=400, maximum=599),
            retry_after=_as_optional_str(raw.get("retry_after"), "retry_after"),
            truncate_at=_as_optional_int(raw.get("truncate_at"), "truncate_at", minimum=0),
            disconnect_before_response=bool(
                _as_optional_bool(
                    raw.get("disconnect_before_response", False),
                    "disconnect_before_response",
                )
            ),
            disconnect_at=_as_optional_int(raw.get("disconnect_at"), "disconnect_at", minimum=0),
            chunk_size=_as_int(raw.get("chunk_size", 16), "chunk_size", minimum=1),
            headers=_string_dict(raw.get("headers", {}), "headers"),
            raw_response_body=_as_optional_str(raw.get("raw_response_body"), "raw_response_body"),
        )


@dataclass(slots=True)
class Scenario:
    key: str = DEFAULT_SCENARIO_KEY
    payload: Any = "mock response"
    behavior: Behavior = field(default_factory=Behavior)


class MockState:
    """Thread-safe scenario registry and redacted request ledger."""

    def __init__(self, *, max_history: int = MAX_REQUEST_HISTORY) -> None:
        self._lock = threading.RLock()
        self._scenarios: dict[str, Scenario] = {}
        self._counters: dict[str, int] = {}
        self._requests: deque[dict[str, Any]] = deque(maxlen=max_history)

    def configure(self, key: str, payload: Any, behavior: Behavior) -> Scenario:
        if not key or not isinstance(key, str):
            raise ValueError("scenario_key must be a non-empty string")
        scenario = Scenario(key=key, payload=payload, behavior=behavior)
        with self._lock:
            self._scenarios[key] = scenario
            self._counters[key] = 0
        return scenario

    def scenario(self, key: str) -> Scenario:
        with self._lock:
            return self._scenarios.get(key, Scenario(key=key))

    def register_call(self, key: str, record: dict[str, Any]) -> int:
        with self._lock:
            count = self._counters.get(key, 0) + 1
            self._counters[key] = count
            safe_record = dict(record)
            safe_record["scenario_key"] = key
            safe_record["call_no"] = count
            self._requests.append(safe_record)
            return count

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._scenarios.clear()
                self._counters.clear()
                self._requests.clear()
                return
            self._scenarios.pop(key, None)
            self._counters.pop(key, None)
            self._requests = deque(
                (r for r in self._requests if r.get("scenario_key") != key),
                maxlen=self._requests.maxlen,
            )

    def counters(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counters)

    def requests(self, key: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            rows = list(self._requests)
        if key is None:
            return rows
        return [row for row in rows if row.get("scenario_key") == key]


class OpenAIMockServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], state: MockState | None = None) -> None:
        self.state = state or MockState()
        super().__init__(server_address, OpenAIMockHandler)


class OpenAIMockHandler(BaseHTTPRequestHandler):
    server: OpenAIMockServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/__mock__/health":
            return self._json(200, {"status": "ok", "service": "openai-mock"})
        if parsed.path == "/__mock__/counters":
            return self._json(200, {"object": "mock.counters", "data": self.server.state.counters()})
        if parsed.path == "/__mock__/requests":
            key = parse_qs(parsed.query).get("scenario_key", [None])[0]
            return self._json(200, {"object": "list", "data": self.server.state.requests(key)})
        if parsed.path == "/v1/models":
            if not self._require_auth():
                return
            key = self._scenario_key()
            call_no = self._record_standard_request(key, body=None, stream=False)
            scenario = self.server.state.scenario(key)
            if self._maybe_failure(scenario, call_no):
                return
            self._delay(scenario.behavior)
            return self._json(
                scenario.behavior.status,
                {
                    "object": "list",
                    "data": [
                        {
                            "id": DEFAULT_MODEL,
                            "object": "model",
                            "created": int(time.time()),
                            "owned_by": "openai-mock",
                            "shutdown_date": None,
                        }
                    ],
                },
                headers=scenario.behavior.headers,
            )
        self._json(404, _error("Not found", "invalid_request_error", "not_found"))

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/__mock__/scenario":
            return self._configure_scenario()
        if parsed.path == "/__mock__/reset":
            return self._reset()
        if parsed.path in {"/v1/responses", "/v1/chat/completions"}:
            if not self._require_auth():
                return
            body = self._read_json_body()
            if body is None:
                return
            if not isinstance(body, dict):
                return self._json(
                    400,
                    _error("Request body must be a JSON object", "invalid_request_error", "invalid_json"),
                )
            if parsed.path == "/v1/responses":
                return self._responses(body)
            return self._chat_completions(body)
        self._json(404, _error("Not found", "invalid_request_error", "not_found"))

    def _configure_scenario(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        if not isinstance(body, dict):
            return self._json(400, {"error": "request body must be an object"})
        try:
            key = body.get("scenario_key", DEFAULT_SCENARIO_KEY)
            if not isinstance(key, str) or not key:
                raise ValueError("scenario_key must be a non-empty string")
            behavior = Behavior.from_dict(body.get("behavior"))
            payload = body.get("payload", "mock response")
            self.server.state.configure(key, payload, behavior)
        except (TypeError, ValueError) as exc:
            return self._json(400, {"error": str(exc)})
        self._json(200, {"object": "mock.scenario", "scenario_key": key, "configured": True})

    def _reset(self) -> None:
        body = self._read_json_body(allow_empty=True)
        if body is None:
            return
        key = None
        if isinstance(body, dict):
            raw_key = body.get("scenario_key")
            if raw_key is not None and not isinstance(raw_key, str):
                return self._json(400, {"error": "scenario_key must be a string"})
            key = raw_key
        self.server.state.reset(key)
        self._json(200, {"object": "mock.reset", "scenario_key": key, "reset": True})

    def _responses(self, request: dict[str, Any]) -> None:
        key = self._scenario_key()
        requested_stream = bool(request.get("stream", False))
        call_no = self._record_standard_request(key, body=request, stream=requested_stream)
        scenario = self.server.state.scenario(key)
        if self._maybe_failure(scenario, call_no):
            return
        self._delay(scenario.behavior)
        if scenario.behavior.disconnect_before_response:
            return self._disconnect_without_response()
        payload = _payload_text(scenario.payload)
        model = str(request.get("model") or DEFAULT_MODEL)
        stream = requested_stream if scenario.behavior.stream is None else scenario.behavior.stream
        if stream:
            return self._responses_stream(model, payload, scenario.behavior)
        return self._send_success_body(_responses_object(model, payload), scenario.behavior)

    def _chat_completions(self, request: dict[str, Any]) -> None:
        key = self._scenario_key()
        requested_stream = bool(request.get("stream", False))
        call_no = self._record_standard_request(key, body=request, stream=requested_stream)
        scenario = self.server.state.scenario(key)
        if self._maybe_failure(scenario, call_no):
            return
        self._delay(scenario.behavior)
        if scenario.behavior.disconnect_before_response:
            return self._disconnect_without_response()
        payload = _payload_text(scenario.payload)
        model = str(request.get("model") or DEFAULT_MODEL)
        stream = requested_stream if scenario.behavior.stream is None else scenario.behavior.stream
        if stream:
            return self._chat_stream(model, payload, scenario.behavior, request)
        return self._send_success_body(_chat_object(model, payload), scenario.behavior)

    def _responses_stream(self, model: str, payload: str, behavior: Behavior) -> None:
        response_id = f"resp_{uuid.uuid4().hex}"
        message_id = f"msg_{uuid.uuid4().hex}"
        created_at = int(time.time())
        in_progress = _responses_object(
            model,
            "",
            response_id=response_id,
            created_at=created_at,
            status="in_progress",
        )
        in_progress["output"] = []
        in_progress["usage"] = None
        completed = _responses_object(
            model,
            payload,
            response_id=response_id,
            created_at=created_at,
        )
        part_empty = {
            "type": "output_text",
            "text": "",
            "annotations": [],
            "logprobs": [],
        }
        part_done = {
            "type": "output_text",
            "text": payload,
            "annotations": [],
            "logprobs": [],
        }
        item_progress = {
            "id": message_id,
            "status": "in_progress",
            "type": "message",
            "role": "assistant",
            "content": [],
        }
        item_done = {
            "id": message_id,
            "status": "completed",
            "type": "message",
            "role": "assistant",
            "content": [part_done],
        }
        events: list[tuple[str, dict[str, Any]]] = []
        seq = 0

        def add(event_type: str, data: dict[str, Any]) -> None:
            nonlocal seq
            seq += 1
            data = dict(data)
            data["type"] = event_type
            data["sequence_number"] = seq
            events.append((event_type, data))

        add("response.created", {"response": in_progress})
        add("response.in_progress", {"response": in_progress})
        add("response.output_item.added", {"output_index": 0, "item": item_progress})
        add(
            "response.content_part.added",
            {
                "item_id": message_id,
                "output_index": 0,
                "content_index": 0,
                "part": part_empty,
            },
        )
        for chunk in _chunks(payload, behavior.chunk_size):
            add(
                "response.output_text.delta",
                {
                    "item_id": message_id,
                    "output_index": 0,
                    "content_index": 0,
                    "delta": chunk,
                    "logprobs": [],
                },
            )
        add(
            "response.output_text.done",
            {
                "item_id": message_id,
                "output_index": 0,
                "content_index": 0,
                "text": payload,
                "logprobs": [],
            },
        )
        add(
            "response.content_part.done",
            {
                "item_id": message_id,
                "output_index": 0,
                "content_index": 0,
                "part": part_done,
            },
        )
        add("response.output_item.done", {"output_index": 0, "item": item_done})
        add("response.completed", {"response": completed})
        self._send_stream_bytes(
            b"".join(_sse_event(event, data) for event, data in events),
            behavior,
        )

    def _chat_stream(
        self,
        model: str,
        payload: str,
        behavior: Behavior,
        request: dict[str, Any],
    ) -> None:
        completion_id = f"chatcmpl-{uuid.uuid4().hex}"
        created = int(time.time())
        frames: list[bytes] = []
        frames.append(
            _chat_sse(
                {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": ""},
                            "logprobs": None,
                            "finish_reason": None,
                        }
                    ],
                }
            )
        )
        for chunk in _chunks(payload, behavior.chunk_size):
            frames.append(
                _chat_sse(
                    {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": chunk},
                                "logprobs": None,
                                "finish_reason": None,
                            }
                        ],
                    }
                )
            )
        frames.append(
            _chat_sse(
                {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {},
                            "logprobs": None,
                            "finish_reason": "stop",
                        }
                    ],
                }
            )
        )
        include_usage = bool((request.get("stream_options") or {}).get("include_usage", False))
        if include_usage:
            frames.append(
                _chat_sse(
                    {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [],
                        "usage": _chat_usage(),
                    }
                )
            )
        frames.append(b"data: [DONE]\n\n")
        self._send_stream_bytes(b"".join(frames), behavior)

    def _maybe_failure(self, scenario: Scenario, call_no: int) -> bool:
        behavior = scenario.behavior
        status = behavior.fail_status if call_no <= behavior.fail_first_n else behavior.status
        if 200 <= status < 300:
            return False
        headers = dict(behavior.headers)
        if behavior.retry_after is not None:
            headers["Retry-After"] = behavior.retry_after
        message, error_type, code = _error_details(status)
        self._json(status, _error(message, error_type, code), headers=headers)
        return True

    def _disconnect_without_response(self) -> None:
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        finally:
            self.connection.close()
            self.close_connection = True

    def _send_success_body(self, body_obj: dict[str, Any], behavior: Behavior) -> None:
        if behavior.raw_response_body is not None:
            raw = behavior.raw_response_body.encode("utf-8")
        else:
            raw = json.dumps(
                body_obj,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        if behavior.truncate_at is not None:
            raw = raw[: behavior.truncate_at]
        self._raw(
            behavior.status,
            raw,
            headers=behavior.headers,
            content_type="application/json",
        )

    def _send_stream_bytes(self, wire: bytes, behavior: Behavior) -> None:
        if behavior.disconnect_at is not None:
            part = wire[: behavior.disconnect_at]
            self.send_response(behavior.status)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            for name, value in behavior.headers.items():
                if name.lower() not in {"content-type", "content-length", "connection"}:
                    self.send_header(name, value)
            self.end_headers()
            try:
                self.wfile.write(part)
                self.wfile.flush()
            finally:
                self.close_connection = True
            return

        self.send_response(behavior.status)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        for name, value in behavior.headers.items():
            if name.lower() not in {"content-type", "content-length", "connection"}:
                self.send_header(name, value)
        self.end_headers()
        self.wfile.write(wire)
        self.wfile.flush()
        self.close_connection = True

    def _record_standard_request(
        self,
        key: str,
        *,
        body: dict[str, Any] | None,
        stream: bool,
    ) -> int:
        authorization = self.headers.get("Authorization")
        scheme = authorization.split(" ", 1)[0] if authorization else None
        header_names = sorted({name.lower() for name in self.headers.keys()})
        redacted_headers = {
            name: ("[REDACTED]" if name.lower() in _SECRET_HEADER_NAMES else value)
            for name, value in self.headers.items()
            if name.lower()
            in {
                "content-type",
                "user-agent",
                "x-mock-scenario-key",
                "authorization",
                "api-key",
                "x-api-key",
            }
        }
        record = {
            "method": self.command,
            "path": urlparse(self.path).path,
            "model": body.get("model") if isinstance(body, dict) else None,
            "stream": bool(stream),
            "authorization": {"present": bool(authorization), "scheme": scheme},
            "header_names": header_names,
            "headers": redacted_headers,
        }
        return self.server.state.register_call(key, record)

    def _scenario_key(self) -> str:
        return self.headers.get("X-Mock-Scenario-Key", DEFAULT_SCENARIO_KEY)

    def _require_auth(self) -> bool:
        value = self.headers.get("Authorization")
        if value and value.lower().startswith("bearer ") and value.split(" ", 1)[1].strip():
            return True
        self._json(
            401,
            _error(
                "Missing or invalid Authorization header",
                "invalid_request_error",
                "invalid_api_key",
            ),
            headers={"WWW-Authenticate": "Bearer"},
        )
        return False

    def _read_json_body(self, *, allow_empty: bool = False) -> Any | None:
        length_header = self.headers.get("Content-Length")
        if length_header is None:
            if allow_empty:
                return {}
            self._json(
                400,
                _error("Missing request body", "invalid_request_error", "invalid_json"),
            )
            return None
        try:
            length = int(length_header)
        except ValueError:
            self._json(
                400,
                _error("Invalid Content-Length", "invalid_request_error", "invalid_json"),
            )
            return None
        raw = self.rfile.read(length)
        if not raw and allow_empty:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json(
                400,
                _error("Invalid JSON body", "invalid_request_error", "invalid_json"),
            )
            return None

    def _delay(self, behavior: Behavior) -> None:
        if behavior.delay_ms:
            time.sleep(behavior.delay_ms / 1000.0)

    def _json(
        self,
        status: int,
        obj: Any,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        raw = json.dumps(
            obj,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self._raw(status, raw, headers=headers, content_type="application/json")

    def _raw(
        self,
        status: int,
        raw: bytes,
        *,
        headers: dict[str, str] | None = None,
        content_type: str,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("x-request-id", f"req_{uuid.uuid4().hex}")
        for name, value in (headers or {}).items():
            if name.lower() not in {"content-type", "content-length"}:
                self.send_header(name, value)
        self.end_headers()
        if raw:
            self.wfile.write(raw)
            self.wfile.flush()


def _responses_object(
    model: str,
    payload: str,
    *,
    response_id: str | None = None,
    created_at: int | None = None,
    status: str = "completed",
) -> dict[str, Any]:
    response_id = response_id or f"resp_{uuid.uuid4().hex}"
    created_at = created_at or int(time.time())
    message_id = f"msg_{uuid.uuid4().hex}"
    output = []
    if status == "completed":
        output = [
            {
                "id": message_id,
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": payload,
                        "annotations": [],
                        "logprobs": [],
                    }
                ],
            }
        ]
    return {
        "id": response_id,
        "object": "response",
        "created_at": created_at,
        "status": status,
        "completed_at": int(time.time()) if status == "completed" else None,
        "error": None,
        "incomplete_details": None,
        "instructions": None,
        "max_output_tokens": None,
        "model": model,
        "output": output,
        "parallel_tool_calls": True,
        "previous_response_id": None,
        "reasoning": {"effort": None, "summary": None},
        "store": False,
        "temperature": 1.0,
        "text": {"format": {"type": "text"}},
        "tool_choice": "auto",
        "tools": [],
        "top_p": 1.0,
        "truncation": "disabled",
        "usage": _responses_usage() if status == "completed" else None,
        "user": None,
        "metadata": {},
    }


def _responses_usage() -> dict[str, Any]:
    return {
        "input_tokens": 0,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens": 0,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": 0,
    }


def _chat_object(model: str, payload: str) -> dict[str, Any]:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": payload,
                    "refusal": None,
                    "annotations": [],
                },
                "logprobs": None,
                "finish_reason": "stop",
            }
        ],
        "usage": _chat_usage(),
        "service_tier": "default",
        "system_fingerprint": None,
    }


def _chat_usage() -> dict[str, Any]:
    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "prompt_tokens_details": {"cached_tokens": 0, "audio_tokens": 0},
        "completion_tokens_details": {
            "reasoning_tokens": 0,
            "audio_tokens": 0,
            "accepted_prediction_tokens": 0,
            "rejected_prediction_tokens": 0,
        },
    }


def _error(
    message: str,
    error_type: str,
    code: str | None,
    param: str | None = None,
) -> dict[str, Any]:
    return {
        "error": {
            "message": message,
            "type": error_type,
            "param": param,
            "code": code,
        }
    }


def _error_details(status: int) -> tuple[str, str, str]:
    if status == 401:
        return "Invalid API key", "invalid_request_error", "invalid_api_key"
    if status == 429:
        return "Rate limit reached", "rate_limit_error", "rate_limit_exceeded"
    if status in {500, 502, 503, 504}:
        return "The server encountered an error", "server_error", "server_error"
    if status == 400:
        return "Bad request", "invalid_request_error", "invalid_request"
    return f"Mock HTTP error {status}", "api_error", f"http_{status}"


def _payload_text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _chunks(text: str, size: int) -> Iterable[str]:
    if not text:
        return []
    return (text[i : i + size] for i in range(0, len(text), size))


def _sse_event(event: str, data: dict[str, Any]) -> bytes:
    encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {encoded}\n\n".encode("utf-8")


def _chat_sse(data: dict[str, Any]) -> bytes:
    encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"data: {encoded}\n\n".encode("utf-8")


def _string_dict(value: Any, name: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TypeError(f"{name} must be an object")
    out: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise TypeError(f"{name} must contain string keys and values")
        out[key] = item
    return out


def _as_int(
    value: Any,
    name: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be <= {maximum}")
    return value


def _as_optional_int(
    value: Any,
    name: str,
    *,
    minimum: int | None = None,
) -> int | None:
    if value is None:
        return None
    return _as_int(value, name, minimum=minimum)


def _as_optional_bool(value: Any, name: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be a boolean")
    return value


def _as_optional_str(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    return value


def create_server(
    host: str = "127.0.0.1",
    port: int = 8000,
    *,
    state: MockState | None = None,
) -> OpenAIMockServer:
    return OpenAIMockServer((host, port), state=state)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="OpenAI-compatible deterministic mock server"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    server = create_server(args.host, args.port)
    host, port = server.server_address
    print(f"OpenAI Mock listening on http://{host}:{port}/v1", flush=True)
    try:
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
