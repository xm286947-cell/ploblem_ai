from __future__ import annotations

from contextlib import asynccontextmanager
import hashlib
from pathlib import PurePath
from typing import Literal
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from fastapi.responses import Response

from . import __version__
from .config import settings
from .contracts import Chunker, Parser, Retriever
from .parsing import FileParser, PlainTextParser, WindowChunker
from .policy import require_public_query, require_public_source
from .providers import OllamaProvider, ProviderUnavailable, UnconfiguredManualProvider
from .retrieval import SQLiteLexicalRetriever
from .store import Store

store = Store(settings.data_dir)
parser: Parser = PlainTextParser()
file_parser = FileParser()
chunker: Chunker = WindowChunker(settings.chunk_size, settings.chunk_overlap)
retriever: Retriever = SQLiteLexicalRetriever(store)
ollama = OllamaProvider(settings)
manual_provider = UnconfiguredManualProvider()


class ImportRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1)
    classification: str
    source_uri: str | None = None
    media_type: str = "text/plain"


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    filters: dict[str, str] = Field(default_factory=dict)
    top_k: int = Field(default=10, ge=1, le=50)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    allowed_source_ids: list[str] | None = None
    filters: dict[str, str] = Field(default_factory=dict)
    mode: Literal["LIVE", "FIXTURE_REPLAY"] = "LIVE"


class ReplayRequest(BaseModel):
    fixture_id: str
    question: str | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="Public Knowledge Service", version=__version__, lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, object]:
    return {"status": "ok", "service": "public-knowledge", "version": __version__, "mode": "LIVE_OR_FIXTURE_REPLAY"}


@app.get("/config")
def config_snapshot() -> dict[str, object]:
    return {"config": settings.public_snapshot(), "config_hash": settings.config_hash()}


@app.get("/providers/ollama/health")
def ollama_health() -> dict[str, object]:
    try:
        version = ollama.version()
    except ProviderUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"provider": ollama.provider_id, "endpoint": settings.ollama_url, "status": "ok", "version": version.get("version")}


@app.get("/providers/ollama/models")
def ollama_models() -> dict[str, object]:
    try:
        models = ollama.models()
    except ProviderUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"provider": ollama.provider_id, "models": [{"name": x.get("name"), "digest": x.get("digest"), "size": x.get("size")} for x in models]}


@app.get("/providers/manual/status")
def manual_status() -> dict[str, str]:
    return {"provider": manual_provider.provider_id, "status": "slot-available-unconfigured", "policy": "fail-closed"}


@app.get("/providers/embedding/status")
def embedding_status() -> dict[str, str]:
    return {"provider": "embedding-unselected", "status": "slot-available-unconfigured", "policy": "not-required-by-lexical-reference"}


@app.post("/sources/import")
def import_source(body: ImportRequest) -> dict[str, object]:
    content_bytes = body.content.encode("utf-8")
    if len(content_bytes) > settings.max_source_bytes:
        raise HTTPException(413, "Source exceeds the configured size limit.")
    require_public_source(body.classification, body.content, body.source_uri)
    try:
        parsed = parser.parse(content_bytes, body.media_type)
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    chunks = chunker.chunk(parsed)
    source_id, revision_id, created = store.import_source(
        body.title, body.source_uri, parsed.text, body.media_type, parsed.parser_id, parsed.parser_version, chunks,
        raw_bytes=content_bytes, locator_ready=parsed.locator_ready, element_counts=parsed.element_counts,
    )
    return {"source_id": source_id, "source_revision": revision_id, "created": created,
            "source_sha256": hashlib.sha256(content_bytes).hexdigest(),
            "parser_snapshot": {"id": parsed.parser_id, "version": parsed.parser_version},
            "element_counts": parsed.element_counts or {}, "locator_status": "READY" if parsed.locator_ready else "PARTIAL_NOT_EVIDENCE_READY",
            "chunk_count": len(chunks)}


@app.post("/sources/import-file")
async def import_file(title: str = Form(..., min_length=1, max_length=300),
                      classification: str = Form(...), file: UploadFile = File(...),
                      source_uri: str | None = Form(default=None)) -> dict[str, object]:
    # Gate metadata before selecting or invoking any parser or provider.
    require_public_source(classification, "", source_uri)
    filename = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not filename or any(ord(char) < 32 for char in filename):
        raise HTTPException(422, "Uploaded file must have a filename.")
    if not title.strip():
        raise HTTPException(422, "Source title must not be blank.")
    content_bytes = await file.read(settings.max_source_bytes + 1)
    if len(content_bytes) > settings.max_source_bytes:
        raise HTTPException(413, "Source exceeds the configured size limit.")
    try:
        file_text = content_bytes.decode("utf-8", "replace")
        require_public_source(classification, file_text, source_uri)
        media_type = file.content_type or FileParser.MEDIA_TYPES.get(PurePath(filename).suffix.lower(), "")
        parsed = file_parser.parse(content_bytes, media_type, filename)
        media_type = FileParser.MEDIA_TYPES[PurePath(filename).suffix.lower()]
    except HTTPException:
        raise
    except Exception as exc:
        # Parser and decoder errors must fail closed, including encrypted/corrupt documents.
        raise HTTPException(422, f"Document parsing failed closed: {exc}") from exc
    chunks = chunker.chunk(parsed) if parsed.locator_ready else []
    source_id, revision_id, created = store.import_source(
        title, source_uri, parsed.text, media_type, parsed.parser_id, parsed.parser_version, chunks,
        raw_bytes=content_bytes, locator_ready=parsed.locator_ready, element_counts=parsed.element_counts,
        original_filename=filename,
    )
    return {"source_id": source_id, "source_revision": revision_id, "created": created,
            "source_sha256": hashlib.sha256(content_bytes).hexdigest(),
            "original_filename": filename, "media_type": media_type,
            "parser_snapshot": {"id": parsed.parser_id, "version": parsed.parser_version},
            "element_counts": parsed.element_counts or {},
            "locator_status": "READY" if parsed.locator_ready else "PARTIAL_NOT_EVIDENCE_READY",
            "chunk_count": len(chunks), "snapshot_url": f"/sources/{source_id}/revisions/{revision_id}/snapshot"}


@app.get("/sources")
def list_sources() -> dict[str, object]:
    return {"sources": store.list_sources()}


@app.get("/sources/{source_id}")
def get_source(source_id: str) -> dict[str, object]:
    result = store.get_source(source_id)
    if result is None:
        raise HTTPException(404, "Source not found.")
    return result


@app.get("/sources/{source_id}/revisions/{revision_id}/snapshot")
def source_snapshot(source_id: str, revision_id: str) -> Response:
    snapshot = store.get_snapshot(source_id, revision_id)
    if snapshot is None:
        raise HTTPException(404, "Original source snapshot not found.")
    content, media_type, filename, raw_sha256 = snapshot
    if hashlib.sha256(content).hexdigest() != raw_sha256:
        raise HTTPException(500, "Original source snapshot integrity check failed.")
    safe_name = filename.replace('"', "").replace("\\", "_").replace("/", "_") or "source"
    disposition = f"inline; filename=\"source\"; filename*=UTF-8''{quote(safe_name, safe='')}"
    return Response(content, media_type=media_type, headers={"Content-Disposition": disposition,
                                                             "X-Source-Snapshot": "immutable",
                                                             "X-Source-SHA256": raw_sha256})


def _search(query: str, top_k: int, source_ids: list[str] | None = None) -> list[dict[str, object]]:
    require_public_query(query)
    hits = retriever.search(query, top_k, source_ids)
    return [{"hit_id": h.hit_id, "source_id": h.source_id, "source_revision": h.source_revision,
             "locator": h.locator, "text": h.text, "score": h.score} for h in hits]


@app.post("/search")
def search(body: SearchRequest) -> dict[str, object]:
    unknown = set(body.filters) - {"source_id"}
    if unknown:
        raise HTTPException(422, "Unsupported filter field.")
    source_ids = [body.filters["source_id"]] if "source_id" in body.filters else None
    return {"hits": _search(body.query, body.top_k, source_ids), "retrieval_snapshot": {"adapter": "sqlite-lexical-reference", "top_k": body.top_k}}


@app.post("/ask")
def ask(body: AskRequest) -> dict[str, object]:
    require_public_query(body.question)
    hits = retriever.search(body.question, 10, body.allowed_source_ids)
    if body.mode == "FIXTURE_REPLAY":
        raise HTTPException(422, "FIXTURE_REPLAY requires POST /fixtures/replay with a fixture_id.")
    try:
        answer, model_snapshot = ollama.generate(body.question, hits)
    except ProviderUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    citations = [{"citation_id": h.hit_id, "source_id": h.source_id, "source_revision": h.source_revision,
                  "locator": h.locator, "text": h.text} for h in hits]
    return {"answer": answer, "citations": citations,
            "source_refs": [{"source_id": h.source_id, "source_revision": h.source_revision} for h in hits],
            "model_snapshot": model_snapshot,
            "retrieval_snapshot": {"adapter": "sqlite-lexical-reference", "top_k": 10}}


@app.get("/citations/{citation_id}")
def citation(citation_id: str) -> dict[str, object]:
    result = store.resolve(citation_id)
    if result is None:
        raise HTTPException(404, "Citation not found.")
    return result


@app.post("/fixtures/capture")
def capture_fixture(body: AskRequest) -> dict[str, object]:
    response = ask(body.model_copy(update={"mode": "LIVE"}))
    return store.capture_fixture({"question": body.question, "allowed_source_ids": body.allowed_source_ids}, response)


@app.get("/fixtures/{fixture_id}")
def get_fixture(fixture_id: str) -> dict[str, object]:
    result = store.get_fixture(fixture_id)
    if result is None:
        raise HTTPException(404, "Fixture not found.")
    return result


@app.post("/fixtures/replay")
def replay_fixture(body: ReplayRequest) -> dict[str, object]:
    fixture = store.get_fixture(body.fixture_id)
    if fixture is None:
        raise HTTPException(404, "Fixture not found.")
    request = fixture["request"]
    if body.question is not None and body.question != request.get("question"):
        raise HTTPException(409, "Replay question does not match the captured request.")
    response = fixture["response"]
    return {**response, "mode": "FIXTURE_REPLAY", "fixture_id": body.fixture_id, "fixture_sha256": fixture["sha256"]}


@app.get("/citations-contract")
def citations_contract() -> dict[str, object]:
    return {"required_fields": ["citation_id", "source_id", "source_revision", "locator", "text"],
            "locator_policy": "source-relative; parser-provided", "resolution": "GET /citations/{citation_id}"}
