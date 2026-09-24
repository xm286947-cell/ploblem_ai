from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class MockProviderState:
    def __init__(self, case: dict[str, Any]):
        self.case = case
        self.calls = 0
        self.requests: list[dict[str, Any]] = []


@contextmanager
def running_openai_mock(case: dict[str, Any]):
    """OpenAI-compatible provider mock.

    Only the Provider/AI boundary is mocked. The product adapter, schema,
    repositories, workflow, publish/query, and evidence chain remain real.
    """
    state = MockProviderState(case)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            raw_request = self.rfile.read(length)
            try:
                request_json = json.loads(raw_request.decode("utf-8"))
            except Exception:
                request_json = {}

            state.calls += 1
            state.requests.append(
                {
                    "method": "POST",
                    "path": self.path,
                    "model": request_json.get("model"),
                    "authorization": self.headers.get("Authorization"),
                    "content_type": self.headers.get("Content-Type"),
                }
            )

            status = int(case.get("http_status", 200))
            if "raw_body" in case:
                body = case["raw_body"]
            else:
                if "content_text" in case:
                    content = str(case["content_text"])
                else:
                    content = json.dumps(case.get("content", {}), ensure_ascii=False)
                body = {
                    "id": "chatcmpl-quality-scenario-test",
                    "object": "chat.completion",
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": content},
                            "finish_reason": case.get("finish_reason", "stop"),
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 100,
                        "total_tokens": 200,
                    },
                }

            encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
