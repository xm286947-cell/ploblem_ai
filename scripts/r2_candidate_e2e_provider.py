from __future__ import annotations

import argparse
import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


EMMC_FIELDS = [
    "manufacturer",
    "product_family",
    "covered_part_numbers",
    "document_number",
    "revision",
    "revision_date",
    "capacity",
    "emmc_version",
    "native_nand_type",
    "partition_storage_mode",
    "boot_area_1",
    "boot_area_2",
    "rpmb",
    "general_purpose_partition",
    "enhanced_user_data_area",
    "default_user_data_area",
    "enhanced_area_supported",
    "pe_cycle",
    "data_retention",
    "endurance_condition",
    "device_life_time_est_typ_a",
    "device_life_time_est_typ_b",
    "pre_eol_info",
    "bkops_status",
    "vendor_proprietary_health_report",
    "vendor_health_monitoring",
    "access_method",
    "bad_block_count",
    "erase_cycle_count",
    "erase_cycle_granularity",
    "reliable_write",
    "bkops",
    "cache",
    "sanitize",
    "power_off_notification",
    "error_reporting",
    "field_firmware_update",
]

DIAGNOSTIC_FIELDS = {
    "device_life_time_est_typ_a",
    "device_life_time_est_typ_b",
    "pre_eol_info",
    "bkops_status",
    "vendor_proprietary_health_report",
    "vendor_health_monitoring",
    "access_method",
    "bad_block_count",
    "erase_cycle_count",
    "erase_cycle_granularity",
}
REQUIREMENT_FIELDS = {
    "reliable_write",
    "bkops",
    "cache",
    "sanitize",
    "power_off_notification",
    "error_reporting",
    "field_firmware_update",
}

FOUND = {
    "manufacturer": ("Demo Storage", None, "Vendor: Demo Storage"),
    "product_family": ("SYN-EMMC-1", None, "Model: SYN-EMMC-1"),
    "covered_part_numbers": ("SYN-EMMC-1", None, "Model: SYN-EMMC-1"),
    "capacity": (64, "GB", "Capacity: 64 GB"),
    "emmc_version": ("5.1", None, "Interface: eMMC 5.1"),
    "device_life_time_est_typ_a": (
        "supported",
        None,
        "Device Life Time Estimation A: supported",
    ),
    "pre_eol_info": ("supported", None, "PRE_EOL_INFO: supported"),
}


def _walk(value: Any):
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _strings(value: Any) -> str:
    return "\n".join(
        item
        for item in _walk(value)
        if isinstance(item, str)
    )


def _find_first(value: Any, key: str) -> Any:
    for item in _walk(value):
        if isinstance(item, dict) and key in item:
            return item[key]
    return None


def _page_evidence(payload: Any, needle: str) -> tuple[int, str]:
    pages = _find_first(payload, "pages")
    if not isinstance(pages, list):
        return 0, ""
    wanted = needle.casefold()
    for item in pages:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "")
        if wanted not in text.casefold():
            continue
        for line in text.splitlines():
            if wanted in line.casefold():
                quote = line.strip()
                if quote:
                    return int(item.get("page") or 0), quote[:500]
        index = text.casefold().find(wanted)
        if index >= 0:
            start = max(0, index - 80)
            end = min(len(text), index + len(needle) + 80)
            return int(item.get("page") or 0), text[start:end].strip()[:500]
    return 0, ""


def _identity_response(payload: Any) -> dict[str, Any]:
    vendor_page, vendor_quote = _page_evidence(payload, "Demo Storage")
    model_page, model_quote = _page_evidence(payload, "SYN-EMMC-1")
    type_page, type_quote = _page_evidence(payload, "eMMC")
    return {
        "vendor": {
            "value": "Demo Storage" if vendor_quote else "",
            "page": vendor_page,
            "quote": vendor_quote,
            "confidence": 0.99 if vendor_quote else 0.0,
        },
        "model": {
            "value": "SYN-EMMC-1" if model_quote else "",
            "page": model_page,
            "quote": model_quote,
            "confidence": 0.99 if model_quote else 0.0,
        },
        "device_type": {
            "value": "eMMC" if type_quote else "",
            "page": type_page,
            "quote": type_quote,
            "confidence": 0.99 if type_quote else 0.0,
        },
    }


def _document_identity_response() -> dict[str, Any]:
    empty = {"value": "", "page": 0, "quote": "", "confidence": 0.0}
    return {
        "document_number": dict(empty),
        "revision": dict(empty),
        "revision_date": dict(empty),
        "document_status": dict(empty),
        "document_variant": dict(empty),
        "language": {
            "value": "English",
            "page": 0,
            "quote": "",
            "confidence": 0.99,
        },
    }


def _model_response() -> dict[str, Any]:
    return {
        "models": [
            {
                "value": "SYN-EMMC-1",
                "scope": "product family",
                "page": 1,
                "quote": "Model: SYN-EMMC-1",
                "confidence": 0.99,
            }
        ]
    }


def _knowledge_type(field: str) -> str:
    if field in DIAGNOSTIC_FIELDS:
        return "diagnostic_capability"
    if field in REQUIREMENT_FIELDS:
        return "device_requirement"
    return "specification"


def _parameter_response(payload: Any) -> dict[str, Any]:
    field_map = _find_first(payload, "field_map")
    if isinstance(field_map, dict) and field_map:
        fields = [str(key) for key in field_map.keys()]
    else:
        required_fields = _find_first(payload, "required_fields")
        if isinstance(required_fields, list) and required_fields:
            fields = [str(item) for item in required_fields]
        else:
            fields = list(EMMC_FIELDS)

    source_id = str(_find_first(payload, "primary_source_id") or "primary")
    out: list[dict[str, Any]] = []
    for field in fields:
        found = FOUND.get(field)
        if found is None:
            out.append(
                {
                    "field_key": field,
                    "value": None,
                    "unit": None,
                    "condition": None,
                    "scope_type": "product_family",
                    "scope_values": ["SYN-EMMC-1"],
                    "evidence": None,
                    "conflict_evidence": [],
                    "confidence": 0.0,
                    "status": "missing",
                    "derived": False,
                    "knowledge_type": _knowledge_type(field),
                }
            )
            continue

        value, unit, quote = found
        out.append(
            {
                "field_key": field,
                "value": value,
                "unit": unit,
                "condition": None,
                "scope_type": "product_family",
                "scope_values": ["SYN-EMMC-1"],
                "evidence": {
                    "source_id": source_id,
                    "page": 1,
                    "section": "Synthetic eMMC Specification",
                    "quote": quote,
                },
                "conflict_evidence": [],
                "confidence": 0.99,
                "status": "found",
                "derived": False,
                "knowledge_type": _knowledge_type(field),
            }
        )
    return {"fields": out}


def response_for(instructions: str, payload: Any) -> dict[str, Any]:
    text = (instructions + "\n" + _strings(payload)).lower()
    if "identify basic device metadata" in text:
        return _identity_response(payload)
    if "document version metadata" in text:
        return _document_identity_response()
    if "concrete manufacturer model numbers" in text:
        return _model_response()
    if (
        "extract storage-device datasheet facts" in text
        or "required_fields" in text
        or "field_map" in text
        or "storageparameterextractresult" in text
    ):
        return _parameter_response(payload)
    raise ValueError("UNRECOGNIZED_STORAGE_PROVIDER_REQUEST")


class State:
    def __init__(self, log_path: Path):
        self.log_path = log_path
        self.lock = threading.Lock()
        self.count = 0

    def record(self, *, path: str, authorization_present: bool, body: Any) -> None:
        with self.lock:
            self.count += 1
            event = {
                "request_no": self.count,
                "path": path,
                "authorization": "PRESENT" if authorization_present else "ABSENT",
                "contains_acca1_value": False,
                "observed_at": time.time(),
                "request_kind": "storage",
            }
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + "\n")


class Handler(BaseHTTPRequestHandler):
    server: "Server"
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    def _json(self, status: int, body: Any) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            return self._json(200, {"status": "ok"})
        if self.path == "/v1/models":
            auth = bool(self.headers.get("Authorization"))
            self.server.state.record(
                path=self.path,
                authorization_present=auth,
                body=None,
            )
            if not auth:
                return self._json(401, {"error": {"message": "auth required"}})
            return self._json(
                200,
                {
                    "object": "list",
                    "data": [{"id": "candidate-e2e-model", "object": "model"}],
                },
            )
        return self._json(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/chat/completions":
            return self._json(404, {"error": {"message": "not found"}})
        auth = bool(self.headers.get("Authorization"))
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            request = json.loads(raw.decode("utf-8"))
        except Exception:
            return self._json(400, {"error": {"message": "invalid json"}})
        self.server.state.record(
            path=self.path,
            authorization_present=auth,
            body=request,
        )
        if not auth:
            return self._json(401, {"error": {"message": "auth required"}})
        try:
            messages = request.get("messages") or []
            system = "\n".join(
                str(m.get("content") or "")
                for m in messages
                if m.get("role") == "system"
            )
            user = next(
                (m.get("content") for m in messages if m.get("role") == "user"),
                "{}",
            )
            payload = json.loads(user) if isinstance(user, str) else user
            result = response_for(system, payload)
        except Exception as exc:
            return self._json(
                422,
                {"error": {"message": f"{type(exc).__name__}:{exc}"}},
            )
        content = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        return self._json(
            200,
            {
                "id": "chatcmpl-" + uuid.uuid4().hex,
                "object": "chat.completion",
                "created": int(time.time()),
                "model": request.get("model") or "candidate-e2e-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 32,
                    "completion_tokens": 64,
                    "total_tokens": 96,
                },
            },
        )


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], state: State):
        self.state = state
        super().__init__(address, Handler)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args()
    server = Server((args.host, args.port), State(args.log.resolve()))
    print(f"E2E_PROVIDER_URL=http://{args.host}:{args.port}/v1", flush=True)
    try:
        server.serve_forever(poll_interval=0.05)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
