"""Single-origin, fail-closed client for the Public Knowledge consumer API."""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class KnowledgeServiceError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = build_opener(_NoRedirect())
_ID = re.compile(r"^[A-Za-z0-9._~:-]{1,240}$")
_DEFAULT_URL = "http://127.0.0.1:9000"
_ENDPOINT_PATTERNS = {
    "health": re.compile(r"^/(?:api/)?health$"),
    "search": re.compile(r"^/(?:v\d+/knowledge/)?search$"),
    "ask": re.compile(r"^/(?:v\d+/knowledge/)?ask$"),
    "sources": re.compile(r"^/(?:v\d+/knowledge/)?sources$"),
    "source": re.compile(r"^/(?:v\d+/knowledge/)?sources/\{source_id\}$"),
    "revision": re.compile(r"^/(?:v\d+/knowledge/)?sources/\{source_id\}/revisions/\{revision_id\}$"),
    "citation": re.compile(r"^/(?:v\d+/)?citations/\{citation_id\}$"),
    "snapshot": re.compile(r"^/(?:v\d+/knowledge/)?sources/\{source_id\}/revisions/\{revision_id\}/snapshot$"),
}
_CAPABILITIES: dict[str, Any] | None = None
_CAPABILITIES_ORIGIN: str | None = None
_CAPABILITIES_LOCK = threading.Lock()


def canonicalize_service_url(value: str) -> str:
    raw = str(value or "").strip()
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise KnowledgeServiceError("KNOWLEDGE_SERVICE_URL_INVALID", "URL 格式或端口无效。") from exc
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise KnowledgeServiceError(
            "KNOWLEDGE_SERVICE_URL_INVALID",
            "仅接受不带凭证、路径、查询或片段的 HTTP(S) 服务根地址。",
        )
    hostname = parsed.hostname.lower()
    if any(ord(ch) < 33 for ch in hostname):
        raise KnowledgeServiceError("KNOWLEDGE_SERVICE_URL_INVALID", "Host 无效。")
    default_port = 443 if parsed.scheme.lower() == "https" else 80
    host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = host if port in {None, default_port} else f"{host}:{port}"
    return urlunsplit((parsed.scheme.lower(), netloc, "", "", ""))


def _config_file_value() -> str | None:
    path = config_file_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    value = payload.get("KNOWLEDGE_SERVICE_URL") if isinstance(payload, dict) else None
    return value.strip() if isinstance(value, str) and value.strip() else None


def config_file_path() -> Path:
    return Path(__file__).resolve().parents[1] / "config" / "knowledge_service.local.json"


def resolve_service_url() -> str:
    # The single persisted user setting is authoritative. Environment names
    # remain compatibility fallbacks for fresh extracts and deployments.
    configured = (
        _config_file_value()
        or os.getenv("KNOWLEDGE_SERVICE_URL")
        or os.getenv("PUBLIC_KNOWLEDGE_SERVICE_URL")
        or os.getenv("PUBLIC_KNOWLEDGE_API_URL")
        or _DEFAULT_URL
    )
    return canonicalize_service_url(configured)


def save_service_url(value: str) -> str:
    normalized = canonicalize_service_url(value)
    path = config_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=path.name + ".", suffix=".tmp", delete=False,
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(json.dumps(
                {"KNOWLEDGE_SERVICE_URL": normalized}, ensure_ascii=False, indent=2
            ) + "\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)
    reset_capabilities_cache()
    return normalized


def reset_capabilities_cache() -> None:
    global _CAPABILITIES, _CAPABILITIES_ORIGIN
    with _CAPABILITIES_LOCK:
        _CAPABILITIES = None
        _CAPABILITIES_ORIGIN = None


def _validate_capabilities(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("contract") != "knowledge-consumer/v1":
        raise KnowledgeServiceError("KNOWLEDGE_SERVICE_CONTRACT_INVALID", "capabilities contract 无效。")
    endpoints = payload.get("endpoints")
    if not isinstance(endpoints, dict):
        raise KnowledgeServiceError("KNOWLEDGE_SERVICE_CONTRACT_INVALID", "capabilities endpoints 无效。")
    capability_flags = payload.get("capabilities")
    if not isinstance(capability_flags, dict) or any(
        not isinstance(capability_flags.get(key), bool)
        for key in ("health", "search", "ask", "sources", "citation")
    ):
        raise KnowledgeServiceError("KNOWLEDGE_SERVICE_CONTRACT_INVALID", "capabilities 状态字段无效。")
    for key in ("health", "search", "ask", "sources"):
        value = endpoints.get(key)
        if not isinstance(value, str) or not _ENDPOINT_PATTERNS[key].fullmatch(value):
            raise KnowledgeServiceError("KNOWLEDGE_SERVICE_CONTRACT_INVALID", f"capabilities 缺少 {key} endpoint。")
    for key in ("source", "revision", "citation", "snapshot"):
        value = endpoints.get(key)
        if value is not None and (not isinstance(value, str) or not _ENDPOINT_PATTERNS[key].fullmatch(value)):
            raise KnowledgeServiceError("KNOWLEDGE_SERVICE_CONTRACT_INVALID", f"capabilities {key} endpoint 不在 Consumer 路由边界内。")
    if "citation" not in endpoints and not isinstance(payload.get("citation"), bool):
        raise KnowledgeServiceError("KNOWLEDGE_SERVICE_CONTRACT_INVALID", "capabilities citation 状态无效。")
    return payload


@dataclass(frozen=True)
class ServiceCapabilities:
    payload: dict[str, Any]
    mode: str


class KnowledgeServiceClient:
    """Routes all consumer calls through one canonical origin and contract."""

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = canonicalize_service_url(base_url or resolve_service_url())

    def _raw(self, path: str, payload: dict[str, Any] | None = None, *, timeout: float = 8.0,
             attempts: int = 1, binary: bool = False) -> Any:
        if not path.startswith("/") or path.startswith("//"):
            raise KnowledgeServiceError("KNOWLEDGE_SERVICE_RESPONSE_INVALID", "拒绝非根相对 endpoint。")
        url = self.base_url + path
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        req = Request(
            url,
            data=data,
            headers={"Content-Type": "application/json", "Accept": "application/json, application/octet-stream"},
            method="POST" if data is not None else "GET",
        )
        last_error: KnowledgeServiceError | None = None
        for attempt in range(max(1, min(attempts, 2))):
            try:
                with _OPENER.open(req, timeout=timeout) as response:
                    expected_origin = urlsplit(self.base_url)
                    actual_origin = urlsplit(response.geturl())
                    if (actual_origin.scheme, actual_origin.netloc) != (expected_origin.scheme, expected_origin.netloc):
                        raise KnowledgeServiceError("KNOWLEDGE_SERVICE_RESPONSE_INVALID", "响应 Origin 与固定服务地址不一致。")
                    body = response.read(25 * 1024 * 1024 + 1)
                    if len(body) > 25 * 1024 * 1024:
                        raise KnowledgeServiceError("KNOWLEDGE_SERVICE_RESPONSE_INVALID", "响应超过大小限制。")
                    if binary:
                        return body, response.headers.get_content_type(), response.headers.get("Content-Disposition", "")
                    try:
                        result = json.loads(body.decode("utf-8"))
                    except (UnicodeError, ValueError) as exc:
                        raise KnowledgeServiceError("KNOWLEDGE_SERVICE_RESPONSE_INVALID", "响应不是有效 JSON。") from exc
                    return result
            except HTTPError as exc:
                if exc.code in {301, 302, 303, 307, 308}:
                    raise KnowledgeServiceError("KNOWLEDGE_SERVICE_RESPONSE_INVALID", "服务重定向已拒绝。") from exc
                detail = exc.read(800).decode("utf-8", "replace")
                if exc.code >= 500 and attempt + 1 < attempts:
                    last_error = KnowledgeServiceError("KNOWLEDGE_SERVICE_UNREACHABLE", detail)
                    continue
                raise KnowledgeServiceError("KNOWLEDGE_SERVICE_UNREACHABLE", f"HTTP {exc.code}: {detail}") from exc
            except (URLError, TimeoutError, OSError) as exc:
                last_error = KnowledgeServiceError(
                    "KNOWLEDGE_SERVICE_TIMEOUT" if isinstance(exc, TimeoutError) else "KNOWLEDGE_SERVICE_UNREACHABLE",
                    str(getattr(exc, "reason", exc)),
                )
                if attempt + 1 < attempts:
                    continue
                raise last_error from exc
        raise last_error or KnowledgeServiceError("KNOWLEDGE_SERVICE_UNREACHABLE")

    def health(self) -> dict[str, Any]:
        result = self._raw("/health", timeout=8, attempts=2)
        if not isinstance(result, dict) or result.get("status") not in {"ok", "healthy", "ready"}:
            raise KnowledgeServiceError("KNOWLEDGE_SERVICE_RESPONSE_INVALID", "Health 响应无效。")
        return result

    def capabilities(self, *, refresh: bool = False) -> ServiceCapabilities:
        global _CAPABILITIES, _CAPABILITIES_ORIGIN
        with _CAPABILITIES_LOCK:
            if not refresh and _CAPABILITIES is not None and _CAPABILITIES_ORIGIN == self.base_url:
                return ServiceCapabilities(_CAPABILITIES, "DISCOVERED")
        try:
            payload = self._raw("/capabilities", timeout=8, attempts=2)
        except KnowledgeServiceError as exc:
            # Fallback only on a clear missing route. Network failures and
            # malformed responses fail closed rather than guessing a contract.
            if "HTTP 404" not in exc.detail:
                raise
            payload = {
                "service": "public-knowledge",
                "service_version": "legacy",
                "contract": "knowledge-consumer/v1",
                "endpoints": {
                    "health": "/health", "search": "/search", "ask": "/ask",
                    "sources": "/sources", "source": "/sources/{source_id}",
                    "revision": "/sources/{source_id}/revisions/{revision_id}",
                    "citation": "/citations/{citation_id}",
                    "snapshot": "/sources/{source_id}/revisions/{revision_id}/snapshot",
                },
                "capabilities": {
                    "health": True, "search": True, "ask": True,
                    "sources": True, "citation": True,
                },
                "citation": True,
                "source_revision": True,
                "generation": True,
                "read_only_consumer_api": urlsplit(self.base_url).hostname not in {"localhost", "127.0.0.1", "::1"},
            }
            mode = "LEGACY_CONTRACT"
        else:
            payload = _validate_capabilities(payload)
            mode = "DISCOVERED"
        with _CAPABILITIES_LOCK:
            _CAPABILITIES = payload
            _CAPABILITIES_ORIGIN = self.base_url
        return ServiceCapabilities(payload, mode)

    def endpoint(self, name: str, **ids: str) -> str:
        caps = self.capabilities().payload
        template = caps["endpoints"].get(name)
        if not isinstance(template, str):
            raise KnowledgeServiceError("KNOWLEDGE_SERVICE_CAPABILITY_MISSING", name)
        for key, value in ids.items():
            if not _ID.fullmatch(value):
                raise KnowledgeServiceError("KNOWLEDGE_SERVICE_RESPONSE_INVALID", f"{key} 无效。")
            template = template.replace("{" + key + "}", quote(value, safe=""))
        if re.search(r"\{[^}]+\}", template):
            raise KnowledgeServiceError("KNOWLEDGE_SERVICE_CONTRACT_INVALID", "endpoint 参数未绑定。")
        return template

    def request(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        # Consumer paths are mapped to discovered names; write/admin paths are
        # intentionally not exposed by this client.
        mapping = {
            "/health": "health", "/sources": "sources", "/search": "search", "/ask": "ask",
        }
        endpoint = mapping.get(path)
        if endpoint is not None:
            path = self.endpoint(endpoint)
        if path.startswith("/sources/") and "/revisions/" in path and path.endswith("/snapshot"):
            parts = path.strip("/").split("/")
            if len(parts) == 5:
                path = self.endpoint("snapshot", source_id=unquote(parts[1]), revision_id=unquote(parts[3]))
                endpoint = "snapshot"
        elif path.startswith("/sources/") and "/revisions/" in path:
            parts = path.strip("/").split("/")
            if len(parts) == 4 and parts[2] == "revisions":
                path = self.endpoint("revision", source_id=unquote(parts[1]), revision_id=unquote(parts[3]))
                endpoint = "revision"
        elif path.startswith("/sources/"):
            source_id = unquote(path[len("/sources/"):])
            path = self.endpoint("source", source_id=source_id)
            endpoint = "source"
        elif path.startswith("/citations/"):
            citation_id = unquote(path[len("/citations/"):])
            path = self.endpoint("citation", citation_id=citation_id)
            endpoint = "citation"
        if endpoint is None:
            raise KnowledgeServiceError("KNOWLEDGE_SERVICE_CAPABILITY_MISSING", "路径不属于 Consumer API。")
        attempts = 1 if endpoint == "ask" else 2
        if endpoint == "ask":
            try:
                configured_timeout = float(os.getenv("PUBLIC_KNOWLEDGE_GENERATION_TIMEOUT_SECONDS", "120"))
            except (TypeError, ValueError):
                configured_timeout = 120.0
            timeout = min(max(configured_timeout, 15.0), 180.0)
        else:
            timeout = 8
        result = self._raw(path, payload, timeout=timeout, attempts=attempts)
        if endpoint == "health":
            if not isinstance(result, dict) or result.get("status") not in {"ok", "healthy", "ready"}:
                raise KnowledgeServiceError("KNOWLEDGE_SERVICE_RESPONSE_INVALID", "Health 响应无效。")
        elif endpoint == "sources":
            if not isinstance(result, dict) or not isinstance(result.get("sources"), list):
                raise KnowledgeServiceError("KNOWLEDGE_SERVICE_RESPONSE_INVALID", "Sources 响应无效。")
        elif endpoint == "search":
            if not isinstance(result, dict) or not isinstance(result.get("hits"), list):
                raise KnowledgeServiceError("KNOWLEDGE_SERVICE_RESPONSE_INVALID", "Search 响应无效。")
        elif endpoint == "ask":
            if not isinstance(result, dict) or not isinstance(result.get("answer"), str) or not isinstance(result.get("citations", []), list):
                raise KnowledgeServiceError("KNOWLEDGE_SERVICE_RESPONSE_INVALID", "Ask 响应无效。")
        elif not isinstance(result, dict):
            raise KnowledgeServiceError("KNOWLEDGE_SERVICE_RESPONSE_INVALID", f"{endpoint} 响应无效。")
        return result

    def get_snapshot(self, source_id: str, revision_id: str) -> tuple[bytes, str, str]:
        path = self.endpoint("snapshot", source_id=source_id, revision_id=revision_id)
        return self._raw(path, timeout=20, attempts=1, binary=True)

    def search(self, query: str, top_k: int = 10, filters: dict[str, str] | None = None) -> dict[str, Any]:
        return self.request("/search", {"query": query, "top_k": top_k, "filters": filters or {}})

    def ask(self, question: str, **options: Any) -> dict[str, Any]:
        return self.request("/ask", {"question": question, **options})

    def list_sources(self) -> list[dict[str, Any]]:
        return self.request("/sources")["sources"]

    def get_source(self, source_id: str) -> dict[str, Any]:
        return self.request("/sources/" + source_id)

    def get_revision(self, source_id: str, revision_id: str) -> dict[str, Any]:
        return self.request(f"/sources/{source_id}/revisions/{revision_id}")

    def get_citation(self, citation_id: str) -> dict[str, Any]:
        return self.request("/citations/" + citation_id)

    def resolve_citation(self, citation_id: str) -> dict[str, Any]:
        return self.get_citation(citation_id)
