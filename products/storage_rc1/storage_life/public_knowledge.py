"""Storage UI adapter for the independent Public Knowledge service.

The adapter deliberately exposes only the public-service contract and never
reads Storage's private knowledge or candidate stores.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import uuid
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urlparse
from urllib.request import Request, HTTPRedirectHandler, build_opener

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .knowledge_suggestions import (
    PublicKnowledgeSuggestionService,
    SuggestionCreate,
    SuggestionEdit,
    SuggestionError,
)

router = APIRouter(prefix="/api/public-knowledge", tags=["Public Knowledge Workspace"])


class _NoRedirectHandler(HTTPRedirectHandler):
    """Never follow redirects from the approved Public Knowledge origin.

    The adapter is a server-side proxy.  Following a 30x would allow a trusted
    service origin to redirect the Storage process to another network target.
    Public Knowledge API calls are expected to be direct JSON/file endpoints,
    so redirects fail closed.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_NO_REDIRECT_OPENER = build_opener(_NoRedirectHandler())

DEFAULT_URL = (
    os.getenv("PUBLIC_KNOWLEDGE_SERVICE_URL")
    or os.getenv("PUBLIC_KNOWLEDGE_API_URL")
    or "http://127.0.0.1:9000"
)

FIXTURE_SOURCES = [
    {"source_id": "fixture-gd25q64e", "title": "GD25Q64E Datasheet · 演示资料", "publisher": "GigaDevice", "classification": "PUBLIC", "media_type": "text/markdown", "version": "Rev1.6", "revision": "Rev1.6", "updated_at": "2026-10-05", "summary": "演示资料，仅用于验证 Public Knowledge 页面操作。", "content": "# GD25Q64E\n\nPublic demonstration record. Program page size: 256 bytes. Sector erase size: 4 KB. This synthetic example is not a product specification.", "structure": [{"type": "heading", "text": "GD25Q64E"}, {"type": "paragraph", "text": "演示记录；不得作为正式规格事实。"}]},
    {"source_id": "fixture-api-guide", "title": "Public Knowledge API Guide · 演示资料", "publisher": "Storage Public Knowledge", "classification": "PUBLIC", "media_type": "text/markdown", "version": "0.1", "revision": "0.1", "updated_at": "2026-10-05", "summary": "演示搜索、引用定位和来源详情操作。", "content": "Search supports lexical retrieval and citation locators. QA is unavailable in fixture data unless an explicit captured answer exists.", "structure": [{"type": "heading", "text": "API Guide"}, {"type": "paragraph", "text": "演示搜索和引用定位。"}]},
]
FIXTURE_HITS = [{"hit_id": "fixture-citation-page1", "source_id": "fixture-gd25q64e", "source_revision": "Rev1.6", "locator": {"page": 1, "section": "演示记录", "chunk_id": "fixture-chunk-1"}, "text": "演示记录；不得作为正式规格事实。", "score": 0.99}]


_OUTBOUND_SECRET_PATTERNS = [
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.I),
    re.compile(r"\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)\s*[:=]\s*\S+", re.I),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", re.I),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
]
_OUTBOUND_PRIVATE_CONTEXT_PATTERNS = [
    re.compile(r"\b(?:customer|project|internal|confidential|restricted)\b\s*[:：=#-]?\s*\S+", re.I),
    re.compile(r"(?:客户|项目|内部|机密|保密|工单|问题单|现场问题)\s*[:：=#-]?\s*\S+"),
    re.compile(r"\b(?:S/?N|serial(?:\s*(?:number|no\.?))?)\s*[:#=-]?\s*[A-Z0-9-]{4,}\b", re.I),
    re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
]
_OUTBOUND_RUNTIME_VALUE_PATTERN = re.compile(
    r"\b(?:percentage[ _-]*used|data[ _-]*units[ _-]*written|critical[ _-]*warning|"
    r"media[ _-]*errors?|pre[ _-]*eol(?:[ _-]*info)?|device[ _-]*life[ _-]*time|"
    r"ecc[ _-]*(?:corrected|uncorrectable|uncorrected)|bad[ _-]*blocks?)\b\s*[:=]\s*\S+",
    re.I,
)


def _require_public_safe_outbound_query(value: str) -> str:
    query = " ".join(str(value or "").replace("\x00", " ").split()).strip()
    if not query:
        raise HTTPException(422, "Public Knowledge 查询不能为空。")
    if any(pattern.search(query) for pattern in _OUTBOUND_SECRET_PATTERNS):
        raise HTTPException(422, "Public Knowledge 查询包含凭证/密钥信息，已阻止发送。")
    if any(pattern.search(query) for pattern in _OUTBOUND_PRIVATE_CONTEXT_PATTERNS):
        raise HTTPException(422, "Public Knowledge 查询包含客户/项目/内部标识，已阻止发送。")
    if _OUTBOUND_RUNTIME_VALUE_PATTERN.search(query):
        raise HTTPException(422, "Public Knowledge 查询包含 Runtime 实测值，已阻止发送；请只查询公开字段定义和工程背景。")
    return query


class SearchBody(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=10, ge=1, le=50)


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class ContextSearchBody(BaseModel):
    """PUBLIC-safe context from Storage business pages.

    The contract intentionally excludes runtime observations, customer/project
    identifiers, internal incidents, logs and diagnosis text. Only public
    device identity and the parameter/indicator name may be forwarded.
    """

    device_type: str | None = Field(default=None, max_length=100)
    vendor: str | None = Field(default=None, max_length=160)
    model: str | None = Field(default=None, max_length=200)
    parameter_name: str | None = Field(default=None, max_length=200)
    focus: str = Field(default="PARAMETER", max_length=32)
    top_k: int = Field(default=6, ge=1, le=12)


class ImportBody(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1)
    classification: str
    source_uri: str | None = None
    media_type: str = "text/plain"


def _public_source_uri(value: str | None) -> str | None:
    """Validate a public-source URL as metadata only; Storage never fetches it."""
    raw = str(value or "").strip()
    if not raw:
        return None
    if len(raw) > 2048:
        raise HTTPException(422, "公开资料来源 URL 过长。")
    try:
        parsed = urlparse(raw)
        port = parsed.port
    except ValueError as exc:
        raise HTTPException(422, "公开资料来源 URL 无效。") from exc
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise HTTPException(422, "公开资料来源 URL 仅允许无凭证的 HTTP(S) 地址。")
    sensitive_query_keys = {
        "key", "api_key", "apikey", "token", "access_token", "auth",
        "password", "secret", "credential", "signature", "sig",
    }
    if any(
        str(key or "").lower() in sensitive_query_keys
        for key, _ in parse_qsl(parsed.query, keep_blank_values=True)
    ):
        raise HTTPException(422, "公开资料来源 URL 含凭证/签名类查询参数，已阻止展示。")
    host = parsed.hostname.lower()
    if host in {"localhost", "127.0.0.1", "::1"} or host.endswith((".local", ".internal")):
        raise HTTPException(422, "公开资料来源 URL 不能指向本地或内部主机。")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise HTTPException(422, "公开资料来源 URL 不能使用私有或保留 IP。")
    host_text = f"[{host}]" if ":" in host and not host.startswith("[") else host
    authority = host_text + (f":{port}" if port is not None else "")
    path = parsed.path or ""
    query = f"?{parsed.query}" if parsed.query else ""
    # Fragments are client-side navigation only; omit them from stored/displayed
    # canonical metadata to avoid carrying opaque tokens in source URLs.
    return f"{parsed.scheme.lower()}://{authority}{path}{query}"


def _canonical_service_url(value: str) -> str:
    base = str(value or "").strip().rstrip("/")
    parsed = urlparse(base)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise HTTPException(
            422,
            "Public Knowledge API 地址必须是服务端批准的 HTTP(S) 根地址，且不能包含凭证、路径、查询或片段。",
        )
    try:
        port = parsed.port
    except ValueError as exc:
        raise HTTPException(422, "Public Knowledge API 端口无效。") from exc
    host = (parsed.hostname or "").lower()
    if not host:
        raise HTTPException(422, "Public Knowledge API 主机无效。")
    default_port = 443 if parsed.scheme == "https" else 80
    effective_port = port or default_port
    host_text = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"{parsed.scheme}://{host_text}:{effective_port}"


def _allowed_service_urls() -> set[str]:
    configured = [DEFAULT_URL]
    configured.extend(
        item.strip()
        for item in os.getenv("PUBLIC_KNOWLEDGE_ALLOWED_URLS", "").split(",")
        if item.strip()
    )
    allowed: set[str] = set()
    for item in configured:
        try:
            allowed.add(_canonical_service_url(item))
        except HTTPException:
            # Invalid server configuration must never broaden browser access.
            continue
    return allowed


def _url(value: str | None) -> str:
    requested = _canonical_service_url(value or DEFAULT_URL)
    allowed = _allowed_service_urls()
    if requested not in allowed:
        raise HTTPException(
            403,
            "Public Knowledge API 地址未被服务端批准；请使用部署配置中的 Public Knowledge 服务地址。",
        )
    return requested


def _request(mode: str, path: str, payload: dict | None = None, base_url: str | None = None):
    if mode == "FIXTURE_REPLAY":
        return None
    if mode != "LIVE":
        raise HTTPException(422, "未知运行模式。")
    url = _url(base_url) + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST" if data is not None else "GET")
    try:
        with _NO_REDIRECT_OPENER.open(req, timeout=8) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:1000]
        raise HTTPException(exc.code, detail or f"Public Knowledge API 返回 HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        raise HTTPException(503, f"Public Knowledge API 不可用：{exc}") from exc


def _request_file(mode: str, path: str, fields: dict[str, str], filename: str, content: bytes,
                  media_type: str, base_url: str | None = None):
    if mode != "LIVE":
        raise HTTPException(409, "演示回放为只读模式；切换到 LIVE 才能提交公开资料。")
    boundary = "----StoragePublicKnowledge" + uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.extend([f"--{boundary}\r\n".encode(),
                      f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                      value.encode("utf-8"), b"\r\n"])
    safe_filename = filename.replace('"', "").replace("\r", "").replace("\n", "")
    parts.extend([f"--{boundary}\r\n".encode(),
                  f'Content-Disposition: form-data; name="file"; filename="{safe_filename}"\r\n'.encode(),
                  f"Content-Type: {media_type}\r\n\r\n".encode(), content, b"\r\n",
                  f"--{boundary}--\r\n".encode()])
    req = Request(_url(base_url) + path, data=b"".join(parts),
                  headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST")
    try:
        with _NO_REDIRECT_OPENER.open(req, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:1000]
        raise HTTPException(exc.code, detail or f"Public Knowledge API 返回 HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        raise HTTPException(503, f"Public Knowledge API 不可用：{exc}") from exc


@router.get("/status")
def status(mode: str = "FIXTURE_REPLAY", base_url: str | None = None):
    if mode == "FIXTURE_REPLAY":
        return {
            "mode": mode,
            "connected": True,
            "search_ready": True,
            "citation_ready": True,
            "retrieval_ready": True,
            "source_count": len(FIXTURE_SOURCES),
            "service": "本地演示回放",
            "llm": "演示数据，不调用模型",
            "model_name": "Not applicable · fixture replay",
            "credential_status": "not_reported",
            "parser_status": "not_reported",
        }
    h = _request(mode, "/health", base_url=base_url)
    connected = h.get("status") == "ok"

    # Retrieval/Search readiness must not depend on the generation provider
    # configuration endpoint.  Query the source catalog independently so the
    # UI can distinguish "service process is alive" from "Public Knowledge is
    # actually usable with indexed public material".
    source_count = 0
    search_ready = False
    citation_ready = False
    retrieval_ready = False
    try:
        source_result = _request(mode, "/sources", base_url=base_url)
        source_items = source_result.get("sources") if isinstance(source_result, dict) else None
        if isinstance(source_items, list):
            source_count = len(source_items)

        # MVP readiness requires an actual indexed source -> Search hit ->
        # resolvable Citation path.  A source title is not guaranteed to appear
        # verbatim inside parsed chunks, so probe several real source identities
        # instead of letting the first catalog row create a false negative.
        probe_candidates: list[str] = []
        for raw_source in (source_items or [])[:5]:
            if not isinstance(raw_source, dict):
                continue
            values = [
                raw_source.get("title"),
                raw_source.get("source_uri"),
                raw_source.get("source_id"),
            ]
            query = " ".join(
                str(value).strip()
                for value in values
                if isinstance(value, str) and value.strip()
            )[:600]
            if query and query not in probe_candidates:
                probe_candidates.append(query)

        for probe_query in probe_candidates:
            probe = _request(
                mode,
                "/search",
                {"query": probe_query, "top_k": 1},
                base_url=base_url,
            )
            hits = probe.get("hits") if isinstance(probe, dict) else None
            if not isinstance(hits, list) or not hits:
                continue
            search_ready = True
            first_hit = hits[0] if isinstance(hits[0], dict) else {}
            citation_id = str(first_hit.get("hit_id") or first_hit.get("citation_id") or "")
            if not citation_id:
                continue
            citation_probe = _request(
                mode,
                "/citations/" + quote(citation_id, safe=""),
                base_url=base_url,
            )
            citation_ready = bool(
                isinstance(citation_probe, dict)
                and citation_probe.get("source_id")
                and citation_probe.get("source_revision")
                and citation_probe.get("locator") is not None
                and citation_probe.get("text") is not None
            )
            if citation_ready:
                break

        retrieval_ready = connected and source_count > 0 and search_ready and citation_ready
    except HTTPException:
        retrieval_ready = False

    cfg: dict[str, Any] = {}
    try:
        cfg = _request(mode, "/config", base_url=base_url)
    except HTTPException:
        # Generation/config observability is optional for Search + Citation.
        cfg = {}
    config = cfg.get("config") if isinstance(cfg.get("config"), dict) else {}
    model_name = next((config.get(key) for key in ("ollama_model", "generation_model", "model_name") if isinstance(config.get(key), str) and config.get(key).strip()), None)
    credential_status = next((config.get(key) for key in ("credential_status", "provider_credential_status") if isinstance(config.get(key), str) and config.get(key) in {"configured", "missing", "not_required", "not_reported"}), "not_reported")
    parser_status = next((config.get(key) for key in ("parser_status", "document_parser_status") if isinstance(config.get(key), str) and config.get(key) in {"ready", "degraded", "unavailable", "not_reported"}), "not_reported")
    # Never return provider URLs, environment values, or credentials to the UI.
    return {
        "mode": "LIVE",
        "connected": connected,
        "search_ready": search_ready,
        "citation_ready": citation_ready,
        "retrieval_ready": retrieval_ready,
        "source_count": source_count,
        "service": h.get("service"),
        "version": h.get("version"),
        "config_hash": cfg.get("config_hash"),
        "llm": "Generation 配置与 Retrieval 分离；未读取或展示凭证",
        "model_name": model_name or "Not Reported",
        "credential_status": credential_status,
        "parser_status": parser_status,
    }


@router.get("/provider-health")
def provider_health(mode: str = "FIXTURE_REPLAY", base_url: str | None = None):
    if mode == "FIXTURE_REPLAY":
        return {"status": "not_called", "message": "演示模式不调用模型"}
    result = _request(mode, "/providers/ollama/health", base_url=base_url)
    return {"status": result.get("status", "unknown"), "provider": result.get("provider"), "version": result.get("version")}


@router.get("/sources")
def sources(mode: str = "FIXTURE_REPLAY", base_url: str | None = None):
    if mode == "FIXTURE_REPLAY":
        return {"sources": FIXTURE_SOURCES, "mode": mode}
    result = _request(mode, "/sources", base_url=base_url)
    return {**result, "mode": mode}


@router.get("/sources/{source_id}")
def source_detail(source_id: str, mode: str = "FIXTURE_REPLAY", base_url: str | None = None):
    if mode == "FIXTURE_REPLAY":
        item = next((x for x in FIXTURE_SOURCES if x["source_id"] == source_id), None)
        if item is None:
            raise HTTPException(404, "演示资料不存在")
        return {"source": item, "revisions": [{"source_revision": item["version"], "media_type": item["media_type"]}], "mode": mode}
    return {**_request(mode, "/sources/" + quote(source_id, safe=""), base_url=base_url), "mode": mode}


@router.post("/sources/import")
def import_source(body: ImportBody, mode: str = "FIXTURE_REPLAY", base_url: str | None = None):
    if body.classification.upper() != "PUBLIC":
        raise HTTPException(422, "仅允许导入明确标记为 PUBLIC 的资料。")
    if mode == "FIXTURE_REPLAY":
        raise HTTPException(409, "演示回放为只读模式；切换到 LIVE 才能提交公开资料。")
    payload = body.model_dump()
    payload["source_uri"] = _public_source_uri(body.source_uri)
    return _request(mode, "/sources/import", payload, base_url)


@router.post("/sources/import-file")
async def import_file(title: str = Form(...), classification: str = Form(...), file: UploadFile = File(...),
                      source_uri: str | None = Form(default=None), mode: str = "FIXTURE_REPLAY",
                      base_url: str | None = None):
    # Enforce the PUBLIC boundary before reading or forwarding file bytes.
    if classification.strip().upper() != "PUBLIC":
        raise HTTPException(422, "仅允许导入明确标记为 PUBLIC 的资料。")
    if mode == "FIXTURE_REPLAY":
        raise HTTPException(409, "演示回放为只读模式；切换到 LIVE 才能提交公开资料。")
    filename = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    if not filename:
        raise HTTPException(422, "请选择有文件名的资料。")
    content = await file.read(25 * 1024 * 1024 + 1)
    if len(content) > 25 * 1024 * 1024:
        raise HTTPException(413, "资料超过 Storage 导入上限 25 MiB。")
    media_type = file.content_type or "application/octet-stream"
    safe_source_uri = _public_source_uri(source_uri)
    return _request_file(mode, "/sources/import-file",
                         {"title": title.strip(), "classification": classification.strip().upper(),
                          "source_uri": safe_source_uri or ""}, filename, content, media_type, base_url)


@router.get("/sources/{source_id}/revisions/{revision_id}/snapshot")
def source_snapshot(source_id: str, revision_id: str, mode: str = "FIXTURE_REPLAY", base_url: str | None = None):
    if mode == "FIXTURE_REPLAY":
        raise HTTPException(404, "演示资料没有原始文件快照。")
    path = (
        "/sources/" + quote(source_id, safe="")
        + "/revisions/" + quote(revision_id, safe="")
        + "/snapshot"
    )
    url = _url(base_url) + path
    try:
        with _NO_REDIRECT_OPENER.open(Request(url, method="GET"), timeout=20) as upstream:
            content = upstream.read(25 * 1024 * 1024 + 1)
            if len(content) > 25 * 1024 * 1024:
                raise HTTPException(413, "原始快照超过 Storage 下载上限 25 MiB。")

            upstream_type = upstream.headers.get_content_type()
            active_types = {
                "text/html",
                "application/xhtml+xml",
                "image/svg+xml",
                "application/xml",
                "text/xml",
            }
            disposition = upstream.headers.get("Content-Disposition", "inline")
            if "\r" in disposition or "\n" in disposition:
                disposition = "attachment"
            # Public Knowledge may legitimately contain HTML/SVG/XML source
            # documents.  Never replay active content inline under the Storage
            # product origin; that would turn a public source snapshot into a
            # same-origin script surface.
            media_type = upstream_type
            if upstream_type in active_types:
                media_type = "application/octet-stream"
                disposition = (
                    'attachment; filename="public-knowledge-source-'
                    + quote(source_id, safe="")[:80]
                    + '"'
                )

            return Response(
                content,
                media_type=media_type,
                headers={
                    "Content-Disposition": disposition,
                    "X-Content-Type-Options": "nosniff",
                    "Content-Security-Policy": "sandbox; default-src 'none'",
                    "X-Source-Snapshot": upstream.headers.get("X-Source-Snapshot", "unknown"),
                    "X-Source-SHA256": upstream.headers.get("X-Source-SHA256", ""),
                },
            )
    except HTTPError as exc:
        raise HTTPException(exc.code, "Public Knowledge 原始快照不可用。") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise HTTPException(503, f"Public Knowledge API 不可用：{exc}") from exc


@router.post("/search")
def search(body: SearchBody, mode: str = "FIXTURE_REPLAY", base_url: str | None = None):
    safe_query = _require_public_safe_outbound_query(body.query)
    if mode == "FIXTURE_REPLAY":
        hits = FIXTURE_HITS if any(x in safe_query.lower() for x in ("gd25", "page", "sector", "引用", "演示")) else []
        return {"hits": hits[:body.top_k], "retrieval_snapshot": {"adapter": "fixture-replay", "top_k": body.top_k}, "mode": mode}
    return {**_request(mode, "/search", {"query": safe_query, "top_k": body.top_k}, base_url), "mode": mode}


def _clean_public_context(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(str(value).replace("\x00", " ").split()).strip()
    return cleaned[:limit] or None


def _context_query(body: ContextSearchBody) -> tuple[str, str]:
    focus = (body.focus or "PARAMETER").strip().upper()
    if focus not in {"DEVICE", "PARAMETER", "COMPARE", "LIFETIME", "RISK", "DIAGNOSIS", "OPTIMIZATION"}:
        raise HTTPException(422, "未知的公共知识查询场景。")
    # Deliberately whitelist only public device identity + the public
    # parameter/indicator name. There is no generic 'context' field here, so
    # Storage runtime/customer/internal problem data cannot be forwarded by
    # this integration contract.
    tokens = [
        _clean_public_context(body.vendor, 160),
        _clean_public_context(body.model, 200),
        _clean_public_context(body.device_type, 100),
        _clean_public_context(body.parameter_name, 200),
    ]
    query = " ".join(x for x in tokens if x)
    if not query:
        raise HTTPException(422, "缺少可公开检索的器件或参数上下文。")
    return query, focus


def _source_for_context(mode: str, source_id: str, base_url: str | None) -> dict[str, Any]:
    if mode == "FIXTURE_REPLAY":
        return next((dict(x) for x in FIXTURE_SOURCES if x["source_id"] == source_id), {})
    raw = _request(mode, "/sources/" + quote(source_id, safe=""), base_url=base_url)
    source = raw.get("source") if isinstance(raw, dict) else None
    return dict(source) if isinstance(source, dict) else {}


def _publisher_from_source(source: dict[str, Any]) -> str:
    publisher = source.get("publisher")
    if isinstance(publisher, str) and publisher.strip():
        return publisher.strip()
    uri = source.get("source_uri") or source.get("official_url")
    if isinstance(uri, str) and uri.strip():
        try:
            host = urlparse(uri).hostname
        except ValueError:
            host = None
        if host:
            return host
    return "Not provided"


@router.post("/context-search")
def context_search(body: ContextSearchBody, mode: str = "LIVE", base_url: str | None = None):
    """Search Public Knowledge from a Storage business page.

    Retrieval/Citation is the required path. Generation is optional,
    best-effort Engineering Context only, and can never turn a RAG answer into
    Storage diagnosis or Formal Evidence.
    """
    query, focus = _context_query(body)
    search_result = search(SearchBody(query=query, top_k=body.top_k), mode=mode, base_url=base_url)
    hits = search_result.get("hits") if isinstance(search_result, dict) else []
    source_cache: dict[str, dict[str, Any]] = {}
    enriched: list[dict[str, Any]] = []
    subject = _clean_public_context(body.parameter_name, 200) or "当前器件"

    for hit in hits or []:
        source_id = str(hit.get("source_id") or "")
        revision = str(hit.get("source_revision") or "")
        if not source_id:
            continue
        source_detail_status = "AVAILABLE"
        if source_id not in source_cache:
            try:
                source_cache[source_id] = _source_for_context(mode, source_id, base_url)
            except HTTPException:
                # Search/Citation is the required product baseline.  Failure of
                # optional Source Detail enrichment must not discard a valid hit.
                source_cache[source_id] = {}
        source = source_cache[source_id]
        if not source:
            source_detail_status = "UNAVAILABLE"
        title = str(source.get("title") or hit.get("source_title") or hit.get("title") or source_id)
        raw_source_uri = (
            source.get("source_uri")
            or source.get("official_url")
            or hit.get("source_uri")
            or hit.get("official_url")
        )
        try:
            source_uri = _public_source_uri(raw_source_uri)
        except HTTPException:
            # Invalid upstream metadata is not a navigable official source.
            source_uri = None
        locator = _normalize_citation_locator({"locator": hit.get("locator")}).get("locator")
        if not isinstance(locator, dict):
            locator = {"raw": str(locator or "")}
        locator_text = json.dumps(locator, ensure_ascii=False, separators=(",", ":"))
        citation_id = str(hit.get("hit_id") or hit.get("citation_id") or "")
        snapshot_url = None
        if mode == "LIVE" and revision:
            snapshot_url = (
                f"/api/public-knowledge/sources/{quote(source_id, safe='')}/revisions/"
                f"{quote(revision, safe='')}/snapshot?mode=LIVE"
            )
        engineering_meaning = (
            f"公开资料用于理解“{subject}”的定义、规格约束与工程背景；"
            "Storage 的寿命、风险和诊断判断仍由 Device Fact / Runtime Observation / Rule / Skill 决定。"
        )
        applicability_boundary = (
            f"适用边界以 {title} / Revision {revision or 'Not provided'} / Locator {locator_text} 为准；"
            "该内容属于 Engineering Context，不等同于 Storage 诊断结论或 Formal Evidence。"
        )
        enriched.append({
            "citation_id": citation_id,
            "source_id": source_id,
            "source_title": title,
            "publisher": (
                _publisher_from_source(source)
                if source else
                str(hit.get("publisher") or "Not provided")
            ),
            "source_detail_status": source_detail_status,
            "source_uri": source_uri,
            "source_revision": revision,
            "original_snippet": hit.get("text") or "",
            "locator": locator,
            "score": hit.get("score"),
            "snapshot_url": snapshot_url,
            "engineering_meaning": engineering_meaning,
            "applicability_boundary": applicability_boundary,
        })

    engineering_context = None
    generation_status = "NOT_REQUESTED"
    generation_citations: list[dict[str, Any]] = []
    if mode == "LIVE" and enriched:
        # Generation is best-effort enrichment only. Retrieval/Citation is the
        # product baseline and must remain usable when the generation provider
        # is unavailable.
        public_question = (
            f"{query}。请只基于公开资料说明工程含义、适用条件和边界；"
            "不要给出 Storage 寿命、风险或诊断结论。"
        )
        try:
            generated = _request(
                mode,
                "/ask",
                {"question": public_question, "mode": "LIVE"},
                base_url,
            )
            answer = generated.get("answer") if isinstance(generated, dict) else None
            if isinstance(answer, str) and answer.strip():
                engineering_context = answer.strip()
                generation_status = "AVAILABLE"
            generation_citations = (
                generated.get("citations") if isinstance(generated, dict)
                and isinstance(generated.get("citations"), list) else []
            )
        except HTTPException as exc:
            if exc.status_code in {502, 503, 504}:
                generation_status = "UNAVAILABLE"
            else:
                # Generation enrichment must never take down public Search.
                generation_status = "DEGRADED"

    return {
        "query": query,
        "focus": focus,
        "mode": mode,
        "generation_provider_used": generation_status == "AVAILABLE",
        "generation_status": generation_status,
        "engineering_context": engineering_context,
        "generation_citations": generation_citations,
        "hits": enriched,
        "retrieval_snapshot": search_result.get("retrieval_snapshot") if isinstance(search_result, dict) else None,
    }


@router.post("/ask")
def ask(body: AskBody, mode: str = "FIXTURE_REPLAY", base_url: str | None = None):
    safe_question = _require_public_safe_outbound_query(body.question)
    if mode == "FIXTURE_REPLAY":
        return {"answer": "演示回放：当前示例资料写明 page size 为 256 bytes。此内容是合成 UI fixture，不是器件规格结论。", "citations": [{"citation_id": "fixture-citation-page1", "source_id": "fixture-gd25q64e", "source_revision": "Rev1.6", "locator": FIXTURE_HITS[0]["locator"], "text": FIXTURE_HITS[0]["text"]}], "mode": mode, "fixture_id": "pk-workspace-qa-001", "answer_scope": "SYNTHETIC_DEMO_ONLY"}
    return {**_request(mode, "/ask", {"question": safe_question, "mode": "LIVE"}, base_url), "mode": mode}


@router.get("/citations/{citation_id}")
def citation(citation_id: str, mode: str = "FIXTURE_REPLAY", base_url: str | None = None):
    if mode == "FIXTURE_REPLAY":
        if citation_id != "fixture-citation-page1":
            raise HTTPException(404, "演示引用不存在")
        return {"citation_id": citation_id, "source_id": "fixture-gd25q64e", "source_revision": "Rev1.6", "locator": FIXTURE_HITS[0]["locator"], "text": FIXTURE_HITS[0]["text"], "mode": mode}
    return {**_request(mode, "/citations/" + quote(citation_id, safe=""), base_url=base_url), "mode": mode}


def _normalize_citation_locator(citation: dict[str, Any]) -> dict[str, Any]:
    locator = citation.get("locator")
    if isinstance(locator, str):
        try:
            parsed_locator = json.loads(locator)
        except (TypeError, ValueError):
            parsed_locator = locator
        if isinstance(parsed_locator, dict):
            return {**citation, "locator": parsed_locator}
    return citation


def _suggestion_service(base_url: str | None = None) -> PublicKnowledgeSuggestionService:
    # This is the same repository root used by Storage's existing Knowledge
    # Production product API and candidate UI.
    from .knowledge_product import repository
    from knowledge_production import BusinessCandidateIntakeService, BusinessEvidenceIntakeService

    repo = repository()

    def resolve_citation(citation_id: str, mode: str):
        citation = _request(mode, "/citations/" + quote(citation_id, safe=""), base_url=base_url)
        normalized = _normalize_citation_locator(citation)
        return {**normalized, "mode": mode}

    def resolve_source(source_id: str, mode: str):
        return {**_request(mode, "/sources/" + quote(source_id, safe=""), base_url=base_url), "mode": mode}

    return PublicKnowledgeSuggestionService(
        repo,
        evidence_intake=BusinessEvidenceIntakeService(repo),
        candidate_intake=BusinessCandidateIntakeService(repo),
        resolve_citation=resolve_citation,
        resolve_source=resolve_source,
    )


def _suggestion_error(exc: SuggestionError) -> HTTPException:
    blocked = {
        "DEMO_ONLY_CANNOT_HANDOFF", "DEMO_ONLY_BLOCKED", "SUGGESTION_STATE_INVALID",
        "SUGGESTION_NOT_READY_FOR_HANDOFF", "HANDED_OFF_SUGGESTION_IMMUTABLE",
        "SOURCE_NOT_PUBLIC", "SOURCE_UNAVAILABLE", "SOURCE_REVISION_MISMATCH",
        "SOURCE_LOCATOR_REQUIRED", "SOURCE_LOCATOR_MISMATCH", "CITATION_UNRESOLVED",
        "SOURCE_IDENTITY_MISMATCH", "SOURCE_ID_MISMATCH", "EVIDENCE_TEXT_UNAVAILABLE",
    }
    status_code = 404 if exc.code == "SUGGESTION_NOT_FOUND" else 409 if exc.code in blocked or "STATE" in exc.code else 422
    return HTTPException(status_code, detail={"code": exc.code, "message": exc.code})


@router.get("/suggestions")
def list_suggestions():
    return {"suggestions": _suggestion_service().list()}


@router.post("/suggestions", status_code=201)
def create_suggestion(body: SuggestionCreate):
    try:
        return _suggestion_service().create(body)
    except SuggestionError as exc:
        raise _suggestion_error(exc) from exc


@router.get("/suggestions/{suggestion_id}")
def get_suggestion(suggestion_id: str):
    try:
        return _suggestion_service().get(suggestion_id)
    except SuggestionError as exc:
        raise _suggestion_error(exc) from exc


@router.put("/suggestions/{suggestion_id}")
def edit_suggestion(suggestion_id: str, body: SuggestionEdit):
    try:
        return _suggestion_service().edit(suggestion_id, body)
    except SuggestionError as exc:
        raise _suggestion_error(exc) from exc


@router.delete("/suggestions/{suggestion_id}", status_code=204)
def delete_suggestion(suggestion_id: str):
    try:
        _suggestion_service().delete(suggestion_id)
    except SuggestionError as exc:
        raise _suggestion_error(exc) from exc
    return None


@router.post("/suggestions/{suggestion_id}/validate")
def validate_suggestion(suggestion_id: str, mode: str = "FIXTURE_REPLAY", base_url: str | None = None):
    try:
        return _suggestion_service(base_url).validate(suggestion_id, mode=mode)
    except SuggestionError as exc:
        raise _suggestion_error(exc) from exc


@router.post("/suggestions/{suggestion_id}/handoff")
def handoff_suggestion(suggestion_id: str, mode: str = "FIXTURE_REPLAY", base_url: str | None = None):
    try:
        return _suggestion_service(base_url).handoff(suggestion_id, mode=mode)
    except SuggestionError as exc:
        raise _suggestion_error(exc) from exc
