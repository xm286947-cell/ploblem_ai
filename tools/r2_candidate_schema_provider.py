from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


def _missing_field(key: str) -> dict[str, Any]:
    return {
        "field_key": key,
        "value": None,
        "unit": None,
        "condition": None,
        "scope_type": "product_family",
        "scope_values": [],
        "evidence": None,
        "conflict_evidence": [],
        "confidence": None,
        "status": "missing",
        "derived": False,
        "knowledge_type": "specification",
    }


def _content(body: dict[str, Any]) -> dict[str, Any]:
    messages = body.get("messages") or []
    system = "\n".join(
        str(x.get("content") or "")
        for x in messages
        if isinstance(x, dict) and x.get("role") == "system"
    )
    user_raw = next(
        (
            str(x.get("content") or "")
            for x in messages
            if isinstance(x, dict) and x.get("role") == "user"
        ),
        "{}",
    )
    try:
        payload = json.loads(user_raw)
    except json.JSONDecodeError:
        payload = {}

    business_instructions = str(
        payload.get("instructions") or ""
    )
    provider_payload = payload.get("provider_payload")
    if isinstance(provider_payload, dict):
        business_instructions += "\n" + str(
            provider_payload.get("instructions") or ""
        )
    marker = (system + "\n" + business_instructions).casefold()
    if "identify basic device metadata" in marker:
        empty = {"value": "", "page": 0, "quote": "", "confidence": 0}
        return {"vendor": empty, "model": empty, "device_type": empty}

    if "identify document version metadata" in marker:
        empty = {"value": "", "page": 0, "quote": "", "confidence": 0}
        return {
            "document_number": empty,
            "revision": empty,
            "revision_date": empty,
            "document_status": empty,
            "document_variant": empty,
            "language": empty,
        }

    if "identify concrete manufacturer model numbers" in marker:
        return {"models": []}

    target_fields = payload.get("target_fields")
    if isinstance(target_fields, list) and target_fields:
        keys = [str(x) for x in target_fields if str(x).strip()]
        return {"fields": [_missing_field(x) for x in keys]}

    field_map = payload.get("field_map")
    if isinstance(field_map, dict) and field_map:
        return {"fields": [_missing_field(str(x)) for x in field_map.keys()]}

    # Runtime domain strategy may wrap the business payload.
    if isinstance(provider_payload, dict):
        target_fields = provider_payload.get("target_fields")
        if isinstance(target_fields, list) and target_fields:
            return {
                "fields": [
                    _missing_field(str(x))
                    for x in target_fields
                    if str(x).strip()
                ]
            }
        field_map = provider_payload.get("field_map")
        if isinstance(field_map, dict) and field_map:
            return {
                "fields": [
                    _missing_field(str(x))
                    for x in field_map.keys()
                ]
            }

    raise ValueError(
        "UNSUPPORTED_CANDIDATE_TEST_PROVIDER_REQUEST:"
        + system[:160].replace("\n", " ")
    )


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/v1/chat/completions":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw.decode("utf-8"))
            content = _content(body)
            envelope = {
                "id": "chatcmpl-r2-candidate-e2e",
                "object": "chat.completion",
                "model": body.get("model") or "candidate-test",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                content,
                                ensure_ascii=False,
                            ),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            }
            out = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
        except Exception as exc:
            out = json.dumps(
                {
                    "error": {
                        "message": str(exc),
                        "type": "candidate_test_provider_error",
                        "code": "unsupported_request",
                    }
                }
            ).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

    def log_message(self, fmt: str, *args: Any) -> None:
        print("[candidate-provider] " + (fmt % args), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9011)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(
        f"CANDIDATE_TEST_PROVIDER=http://{args.host}:{args.port}/v1",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
