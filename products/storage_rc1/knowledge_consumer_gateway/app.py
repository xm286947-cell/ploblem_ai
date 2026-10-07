"""Narrow LAN-facing proxy for the existing loopback Public Knowledge service.

This is a transport boundary only: it owns no index, source store, provider,
or generation logic. The existing Public Knowledge service remains the sole RAG.
"""
from __future__ import annotations

import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = build_opener(_NoRedirect())
_ID = re.compile(r"^[A-Za-z0-9._~:-]{1,240}$")
def _configured_upstream() -> str:
    raw = os.getenv("KNOWLEDGE_GATEWAY_UPSTREAM", "http://127.0.0.1:9000").strip().rstrip("/")
    parsed = urlsplit(raw)
    try:
        port = parsed.port
    except ValueError as exc:
        raise RuntimeError("KNOWLEDGE_GATEWAY_UPSTREAM has an invalid port") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username or parsed.password
        or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
    ):
        raise RuntimeError("KNOWLEDGE_GATEWAY_UPSTREAM must be a loopback HTTP(S) service root")
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    return f"{parsed.scheme}://{host}" + (f":{port}" if port else "")


UPSTREAM = _configured_upstream()
_UPSTREAM_ORIGIN = (urlsplit(UPSTREAM).scheme, urlsplit(UPSTREAM).netloc)
app = FastAPI(title="Public Knowledge Consumer Gateway", docs_url=None, redoc_url=None, openapi_url=None)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=10, ge=1, le=50)
    filters: dict[str, str] = Field(default_factory=dict)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    allowed_source_ids: list[str] | None = Field(default=None, max_length=10)
    mode: str = "LIVE"
    response_language: str = "zh-CN"
    include_citation_translations: bool = False


def _valid_id(value: str) -> str:
    if not _ID.fullmatch(value):
        raise HTTPException(400, "Invalid consumer resource identifier.")
    return quote(value, safe="")


def _upstream(path: str, payload: dict | None = None, timeout: float = 8.0, binary: bool = False):
    if not path.startswith("/") or path.startswith("//"):
        raise HTTPException(400, "Invalid upstream route.")
    url = UPSTREAM + path
    parsed = urlsplit(url)
    if (parsed.scheme, parsed.netloc) != _UPSTREAM_ORIGIN:
        raise HTTPException(502, "Upstream origin mismatch.")
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST" if data is not None else "GET")
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            final = urlsplit(response.geturl())
            if (final.scheme, final.netloc) != _UPSTREAM_ORIGIN:
                raise HTTPException(502, "Upstream redirect denied.")
            raw = response.read(25 * 1024 * 1024 + 1)
            if len(raw) > 25 * 1024 * 1024:
                raise HTTPException(413, "Upstream response exceeds gateway limit.")
            if binary:
                return raw, response.headers.get_content_type(), response.headers.get("Content-Disposition", "attachment")
            try:
                result = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeError) as exc:
                raise HTTPException(502, "Invalid upstream JSON response.") from exc
            if not isinstance(result, dict):
                raise HTTPException(502, "Invalid upstream response contract.")
            return result
    except HTTPError as exc:
        if exc.code in {301, 302, 303, 307, 308}:
            raise HTTPException(502, "Upstream redirect denied.") from exc
        detail = exc.read(800).decode("utf-8", "replace")
        raise HTTPException(exc.code, detail or "Public Knowledge upstream error.") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise HTTPException(503, "Public Knowledge upstream unavailable.") from exc


@app.get("/health")
def health():
    upstream = _upstream("/health", timeout=4)
    return {"status": "ok", "service": "public-knowledge-consumer-gateway", "upstream": upstream}


@app.get("/capabilities")
def capabilities():
    upstream = _upstream("/health", timeout=4)
    return {
        "service": "public-knowledge",
        "service_version": upstream.get("version", "unknown"),
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
            "sources": True, "revision": True, "citation": True, "snapshot": True,
        },
        "citation": True,
        "source_revision": True,
        "generation": True,
        "read_only_consumer_api": True,
    }


@app.get("/sources")
def sources():
    return _upstream("/sources")


@app.get("/sources/{source_id}")
def source(source_id: str):
    return _upstream("/sources/" + _valid_id(source_id))


@app.post("/search")
def search(body: SearchRequest):
    return _upstream("/search", body.model_dump(), timeout=8)


@app.post("/ask")
def ask(body: AskRequest):
    # One bounded provider request. The gateway never retries generation.
    return _upstream("/ask", body.model_dump(), timeout=120)


@app.get("/citations/{citation_id}")
def citation(citation_id: str):
    return _upstream("/citations/" + _valid_id(citation_id))


@app.get("/sources/{source_id}/revisions/{revision_id}")
def revision(source_id: str, revision_id: str):
    safe_source_id = _valid_id(source_id)
    _valid_id(revision_id)
    # The current Public Knowledge service exposes revision metadata as part
    # of GET /sources/{source_id}; its standalone revision GET returns 405.
    # Resolve from that read-only source response instead of advertising a
    # route the upstream cannot serve.
    result = _upstream("/sources/" + safe_source_id)
    source_payload = result.get("source")
    revisions = result.get("revisions")
    if not isinstance(source_payload, dict) or not isinstance(revisions, list):
        raise HTTPException(502, "Invalid Public Knowledge source revision response.")
    if str(source_payload.get("source_id") or "") != source_id:
        raise HTTPException(502, "Public Knowledge source identity mismatch.")
    for item in revisions:
        if not isinstance(item, dict):
            continue
        item_revision_id = str(
            item.get("revision_id") or item.get("source_revision") or item.get("version") or ""
        )
        if item_revision_id == revision_id:
            return {**item, "source_id": source_id, "revision_id": revision_id}
    raise HTTPException(404, "Public Knowledge source revision not found.")


@app.get("/sources/{source_id}/revisions/{revision_id}/snapshot")
def snapshot(source_id: str, revision_id: str):
    raw, media_type, disposition = _upstream(
        "/sources/" + _valid_id(source_id) + "/revisions/" + _valid_id(revision_id) + "/snapshot",
        timeout=20,
        binary=True,
    )
    return Response(raw, media_type=media_type, headers={"Content-Disposition": disposition, "X-Content-Type-Options": "nosniff"})


@app.api_route("/{unavailable_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
def deny_non_consumer_routes(unavailable_path: str):
    raise HTTPException(404, "Route is not part of the read-only Knowledge Consumer API.")
