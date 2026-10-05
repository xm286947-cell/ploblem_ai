"""Storage UI adapter for the independent Public Knowledge service.

The adapter deliberately exposes only the public-service contract and never
reads Storage's private knowledge or candidate stores.
"""
from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/public-knowledge", tags=["Public Knowledge Workspace"])
DEFAULT_URL = os.getenv("PUBLIC_KNOWLEDGE_API_URL", "http://127.0.0.1:8080")

FIXTURE_SOURCES = [
    {"source_id": "fixture-gd25q64e", "title": "GD25Q64E Datasheet · 演示资料", "publisher": "GigaDevice", "classification": "PUBLIC", "media_type": "text/markdown", "version": "Rev1.6", "revision": "Rev1.6", "updated_at": "2026-10-05", "summary": "演示资料，仅用于验证 Public Knowledge 页面操作。", "content": "# GD25Q64E\n\nPublic demonstration record. Program page size: 256 bytes. Sector erase size: 4 KB. This synthetic example is not a product specification.", "structure": [{"type": "heading", "text": "GD25Q64E"}, {"type": "paragraph", "text": "演示记录；不得作为正式规格事实。"}]},
    {"source_id": "fixture-api-guide", "title": "Public Knowledge API Guide · 演示资料", "publisher": "Storage Public Knowledge", "classification": "PUBLIC", "media_type": "text/markdown", "version": "0.1", "revision": "0.1", "updated_at": "2026-10-05", "summary": "演示搜索、引用定位和来源详情操作。", "content": "Search supports lexical retrieval and citation locators. QA is unavailable in fixture data unless an explicit captured answer exists.", "structure": [{"type": "heading", "text": "API Guide"}, {"type": "paragraph", "text": "演示搜索和引用定位。"}]},
]
FIXTURE_HITS = [{"hit_id": "fixture-citation-page1", "source_id": "fixture-gd25q64e", "source_revision": "Rev1.6", "locator": {"page": 1, "section": "演示记录", "chunk_id": "fixture-chunk-1"}, "text": "演示记录；不得作为正式规格事实。", "score": 0.99}]


class SearchBody(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=10, ge=1, le=50)


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class ImportBody(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1)
    classification: str
    source_uri: str | None = None
    media_type: str = "text/plain"


def _url(value: str | None) -> str:
    base = (value or DEFAULT_URL).rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise HTTPException(422, "Public Knowledge API 地址必须是有效的 HTTP(S) 地址，且不能包含凭证。")
    return base


def _request(mode: str, path: str, payload: dict | None = None, base_url: str | None = None):
    if mode == "FIXTURE_REPLAY":
        return None
    if mode != "LIVE":
        raise HTTPException(422, "未知运行模式。")
    url = _url(base_url) + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST" if data is not None else "GET")
    try:
        with urlopen(req, timeout=8) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:1000]
        raise HTTPException(exc.code, detail or f"Public Knowledge API 返回 HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        raise HTTPException(503, f"Public Knowledge API 不可用：{exc}") from exc


@router.get("/status")
def status(mode: str = "FIXTURE_REPLAY", base_url: str | None = None):
    if mode == "FIXTURE_REPLAY":
        return {"mode": mode, "connected": True, "service": "本地演示回放", "llm": "演示数据，不调用模型", "model_name": "Not applicable · fixture replay", "credential_status": "not_reported", "parser_status": "not_reported"}
    h = _request(mode, "/health", base_url=base_url)
    cfg = _request(mode, "/config", base_url=base_url)
    config = cfg.get("config") if isinstance(cfg.get("config"), dict) else {}
    model_name = next((config.get(key) for key in ("ollama_model", "generation_model", "model_name") if isinstance(config.get(key), str) and config.get(key).strip()), None)
    credential_status = next((config.get(key) for key in ("credential_status", "provider_credential_status") if isinstance(config.get(key), str) and config.get(key) in {"configured", "missing", "not_required", "not_reported"}), "not_reported")
    parser_status = next((config.get(key) for key in ("parser_status", "document_parser_status") if isinstance(config.get(key), str) and config.get(key) in {"ready", "degraded", "unavailable", "not_reported"}), "not_reported")
    # Never return provider URLs, environment values, or credentials to the UI.
    return {"mode": "LIVE", "connected": h.get("status") == "ok", "service": h.get("service"), "version": h.get("version"), "config_hash": cfg.get("config_hash"), "llm": "通过服务健康接口确认；未读取或展示凭证", "model_name": model_name or "Not Reported", "credential_status": credential_status, "parser_status": parser_status}


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
    return {**_request(mode, "/sources/" + source_id, base_url=base_url), "mode": mode}


@router.post("/sources/import")
def import_source(body: ImportBody, mode: str = "FIXTURE_REPLAY", base_url: str | None = None):
    if body.classification.upper() != "PUBLIC":
        raise HTTPException(422, "仅允许导入明确标记为 PUBLIC 的资料。")
    if mode == "FIXTURE_REPLAY":
        raise HTTPException(409, "演示回放为只读模式；切换到 LIVE 才能提交公开资料。")
    return _request(mode, "/sources/import", body.model_dump(), base_url)


@router.post("/search")
def search(body: SearchBody, mode: str = "FIXTURE_REPLAY", base_url: str | None = None):
    if mode == "FIXTURE_REPLAY":
        hits = FIXTURE_HITS if any(x in body.query.lower() for x in ("gd25", "page", "sector", "引用", "演示")) else []
        return {"hits": hits[:body.top_k], "retrieval_snapshot": {"adapter": "fixture-replay", "top_k": body.top_k}, "mode": mode}
    return {**_request(mode, "/search", {"query": body.query, "top_k": body.top_k}, base_url), "mode": mode}


@router.post("/ask")
def ask(body: AskBody, mode: str = "FIXTURE_REPLAY", base_url: str | None = None):
    if mode == "FIXTURE_REPLAY":
        return {"answer": "演示回放：当前示例资料写明 page size 为 256 bytes。此内容是合成 UI fixture，不是器件规格结论。", "citations": [{"citation_id": "fixture-citation-page1", "source_id": "fixture-gd25q64e", "source_revision": "Rev1.6", "locator": FIXTURE_HITS[0]["locator"], "text": FIXTURE_HITS[0]["text"]}], "mode": mode, "fixture_id": "pk-workspace-qa-001", "answer_scope": "SYNTHETIC_DEMO_ONLY"}
    return {**_request(mode, "/ask", {"question": body.question, "mode": "LIVE"}, base_url), "mode": mode}


@router.get("/citations/{citation_id}")
def citation(citation_id: str, mode: str = "FIXTURE_REPLAY", base_url: str | None = None):
    if mode == "FIXTURE_REPLAY":
        if citation_id != "fixture-citation-page1":
            raise HTTPException(404, "演示引用不存在")
        return {"citation_id": citation_id, "source_id": "fixture-gd25q64e", "source_revision": "Rev1.6", "locator": FIXTURE_HITS[0]["locator"], "text": FIXTURE_HITS[0]["text"], "mode": mode}
    return {**_request(mode, "/citations/" + citation_id, base_url=base_url), "mode": mode}
