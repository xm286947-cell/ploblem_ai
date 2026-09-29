from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


STATE = {"requests": 0, "auth_present": 0, "paths": []}
LOCK = threading.Lock()


def _first_type(schema: dict[str, Any]) -> str:
    value = schema.get("type")
    if isinstance(value, list):
        for item in value:
            if item != "null":
                return str(item)
        return "null"
    return str(value or "")


def _string_value(path: tuple[str, ...], schema: dict[str, Any]) -> str:
    key = path[-1] if path else ""
    parent = path[-2] if len(path) > 1 else ""
    if schema.get("enum"):
        return str(schema["enum"][0])
    if key in {"vendor", "manufacturer"} or parent == "vendor":
        return "Samsung"
    if key in {"model", "part_number"} or parent == "model":
        return "KLMBG2JETD-B041"
    if key in {"device_type", "type"} or parent == "device_type":
        return "eMMC"
    if "document_number" in key:
        return "SYNTH-EMMC-001"
    if key in {"revision", "revision_date"}:
        return "Rev 1.0" if key == "revision" else "2026-01-01"
    if key in {"status", "verify_status"}:
        return "FOUND"
    if key in {"canonical_name", "field_key"}:
        return "device_life_time_est_typ_a"
    if key in {"parameter_name", "label"}:
        return "Device Life Time Estimation Type A"
    if key in {"value", "ai_value", "final_value", "proposed_value"}:
        if parent == "vendor":
            return "Samsung"
        if parent == "model":
            return "KLMBG2JETD-B041"
        if parent == "device_type":
            return "eMMC"
        return "1"
    if key in {"unit", "ai_unit", "final_unit", "proposed_unit"}:
        return "%"
    if key in {"quote", "source_text", "message", "summary", "reason", "basis"}:
        return "Synthetic eMMC datasheet evidence"
    if key in {"scope", "condition"}:
        return "synthetic validation fixture"
    return "mock-e2e"


def _resolve_ref(ref: str, root: dict[str, Any]) -> Any:
    if not ref.startswith("#/"):
        return {}
    node: Any = root
    for token in ref[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict):
            return {}
        node = node.get(token)
    return node if isinstance(node, dict) else {}


def find_schema(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        direct = value.get("schema")
        if isinstance(direct, dict):
            return direct
        for child in value.values():
            found = find_schema(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_schema(child)
            if found:
                return found
    return {}


def generate(
    schema: Any,
    path: tuple[str, ...] = (),
    root: dict[str, Any] | None = None,
) -> Any:
    if not isinstance(schema, dict):
        return {}
    root = root or schema
    if "$ref" in schema:
        return generate(_resolve_ref(str(schema["$ref"]), root), path, root)
    if "const" in schema:
        return schema["const"]
    if schema.get("enum"):
        return schema["enum"][0]
    for union_key in ("oneOf", "anyOf"):
        options = schema.get(union_key)
        if isinstance(options, list):
            for option in options:
                if isinstance(option, dict) and _first_type(option) != "null":
                    return generate(option, path, root)
    stype = _first_type(schema)
    if stype == "object" or schema.get("properties") is not None:
        props = schema.get("properties") or {}
        required = list(schema.get("required") or [])
        keys = list(dict.fromkeys(required + list(props.keys())))
        return {
            key: generate(props.get(key, {}), path + (str(key),), root)
            for key in keys
        }
    if stype == "array":
        item_schema = schema.get("items") or {}
        minimum = int(schema.get("minItems") or 0)
        count = max(1, minimum)
        maximum = schema.get("maxItems")
        if isinstance(maximum, int):
            count = min(count, maximum)
        return [generate(item_schema, path + ("item",), root) for _ in range(count)]
    if stype == "integer":
        key = path[-1] if path else ""
        return 1 if key != "confidence" else 1
    if stype == "number":
        key = path[-1] if path else ""
        return 0.98 if "confidence" in key else 1.0
    if stype == "boolean":
        return False
    if stype == "null":
        return None
    return _string_value(path, schema)


class Handler(BaseHTTPRequestHandler):
    def _json(self, status: int, payload: Any) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        if self.path == "/__e2e__/health":
            return self._json(200, {"status": "ok"})
        if self.path == "/__e2e__/stats":
            with LOCK:
                return self._json(200, dict(STATE))
        return self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        if self.path != "/v1/chat/completions":
            return self._json(404, {"error": "not_found"})
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        auth = self.headers.get("Authorization", "")
        messages = body.get("messages") or []
        user_content = messages[-1].get("content", "{}") if messages else "{}"
        try:
            business = json.loads(user_content)
        except Exception:
            business = {}
        schema = find_schema(business)
        payload = generate(schema or {"type": "object"})
        with LOCK:
            STATE["requests"] += 1
            STATE["auth_present"] += int(auth.startswith("Bearer ") and len(auth) > 7)
            STATE["paths"].append(self.path)
        completion = {
            "id": "chatcmpl-r2-e2e",
            "object": "chat.completion",
            "created": 1,
            "model": body.get("model") or "mock-gpt",
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": json.dumps(payload, ensure_ascii=False),
                },
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 10,
                "total_tokens": 20,
            },
        }
        return self._json(200, completion)

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18081)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"R2_E2E_MOCK=http://{args.host}:{args.port}/v1", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
