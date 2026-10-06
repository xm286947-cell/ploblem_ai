from __future__ import annotations

import asyncio
import ipaddress
import os
import secrets
import signal
from contextlib import asynccontextmanager
import hashlib
from pathlib import Path
from pathlib import PurePath
from typing import Literal
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi import BackgroundTasks, Request
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse, Response

from . import __version__
from .admin_config import ConfigValidationError, ConfigurationAdmin
from .config import Settings
from .contracts import Chunker, Parser, Retriever
from .parsing import FileParser, PlainTextParser, WindowChunker
from .policy import require_public_query, require_public_source
from .providers import OpenAICompatibleProvider, OllamaProvider, ProviderUnavailable, UnconfiguredManualProvider
from .retrieval import SQLiteLexicalRetriever
from .store import Store

admin_config = ConfigurationAdmin()
settings = admin_config.effective
store = Store(settings.data_dir)
parser: Parser = PlainTextParser()
file_parser = FileParser()
chunker: Chunker = WindowChunker(settings.chunk_size, settings.chunk_overlap)
retriever: Retriever = SQLiteLexicalRetriever(store)
ollama = OllamaProvider(settings)
active_api_key = admin_config.secret_store.effective_key()
openai_compatible = OpenAICompatibleProvider(settings, active_api_key)
manual_provider = UnconfiguredManualProvider()
service_instance_id = secrets.token_urlsafe(12)


def active_generation_provider():
    """Select exactly the configured generation provider; never fall back."""
    if settings.provider_type == "ollama":
        return ollama
    if settings.provider_type == "openai_compatible":
        return openai_compatible
    raise ProviderUnavailable("Configured generation provider is unavailable.", "PROVIDER_UNREACHABLE")


class ImportRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    publisher: str | None = Field(default=None, max_length=200)
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
    response_language: Literal["AUTO", "zh-CN", "en"] = "AUTO"
    include_citation_translations: bool = False


class ReplayRequest(BaseModel):
    fixture_id: str
    question: str | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="Public Knowledge Service", version=__version__, lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, object]:
    return {"status": "ok", "service": "public-knowledge", "version": __version__, "mode": "LIVE_OR_FIXTURE_REPLAY", "instance_id": service_instance_id}


@app.get("/settings", include_in_schema=False)
def admin_settings_page() -> FileResponse:
    return FileResponse(Path(__file__).with_name("admin.html"), media_type="text/html; charset=utf-8")


def _admin_state(request: Request | None = None) -> dict[str, object]:
    state = admin_config.state(
        service_version=__version__,
        embedding_status=embedding_status()["status"],
        retrieval_adapter="sqlite-lexical-reference",
        active_credential=active_api_key,
    )
    strategy = _restart_strategy()
    state["restart_capability"] = {
        "strategy": strategy if strategy == "SUPERVISED_PROCESS_EXIT" else "UNAVAILABLE",
        "available": strategy == "SUPERVISED_PROCESS_EXIT" and (request is None or _request_is_loopback(request)),
    }
    return state


def _restart_strategy() -> str:
    configured = os.getenv("PKR_RESTART_STRATEGY", "UNAVAILABLE").strip().lower()
    return "SUPERVISED_PROCESS_EXIT" if configured == "supervised_process_exit" else "UNAVAILABLE"


def _request_is_loopback(request: Request) -> bool:
    host = request.url.hostname or ""
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


async def _terminate_after_response() -> None:
    # FastAPI runs BackgroundTasks after sending the response body. Uvicorn then
    # handles SIGTERM gracefully; an external supervisor may restart this PID.
    await asyncio.sleep(0.35)
    os.kill(os.getpid(), signal.SIGTERM)


@app.get("/admin/config")
def admin_get_config(request: Request) -> dict[str, object]:
    return _admin_state(request)


@app.get("/admin/effective-config")
def admin_get_effective_config(request: Request) -> dict[str, object]:
    state = _admin_state(request)
    return {"current_effective_config": state["current_effective_config"], "apply_state": state["apply_state"]}


@app.post("/admin/config/validate")
def admin_validate_config(body: dict[str, object]) -> dict[str, object]:
    # Credentials are accepted only by Test/Save and never enter validation
    # output, persisted configuration, or the non-secret config hash.
    body = {key: value for key, value in body.items() if key not in {"api_key", "openai_api_key", "clear_credential"}}
    try:
        candidate = admin_config.validate(body)
    except ConfigValidationError as exc:
        raise HTTPException(422, {"errors": exc.errors}) from exc
    candidate_settings = Settings.with_overrides(admin_config.base, candidate)
    return {"valid": True, "config": candidate, "config_hash": candidate_settings.config_hash()}


@app.post("/admin/provider/test")
def admin_test_provider(body: dict[str, object]) -> dict[str, object]:
    candidate_key = str(body.get("api_key") or body.get("openai_api_key") or "")
    clear_local = body.get("clear_credential") is True
    config_payload = body.get("config") if isinstance(body.get("config"), dict) else body
    candidate_payload = {key: value for key, value in config_payload.items() if key not in {"api_key", "openai_api_key", "clear_credential"}}
    try:
        candidate = admin_config.validate(candidate_payload)
    except ConfigValidationError as exc:
        raise HTTPException(422, {"errors": exc.errors}) from exc
    candidate_settings = Settings.with_overrides(admin_config.base, candidate)
    if candidate_settings.provider_type == "openai_compatible":
        credential = admin_config.effective_credential(candidate_key=candidate_key, clear_local=clear_local)
        if not credential:
            raise HTTPException(503, {"code": "CREDENTIAL_MISSING", "message": "API credential is missing. Enter a key or configure OPENAI_COMPATIBLE_API_KEY."})
        candidate_provider = OpenAICompatibleProvider(candidate_settings, credential)
        try:
            result = candidate_provider.test_connection()
        except ProviderUnavailable as exc:
            raise HTTPException(503, {"code": exc.code, "message": str(exc)}) from exc
        test_id = admin_config.record_provider_test(candidate, credential, clear_local)
        return {
            "ok": True,
            "test_id": test_id,
            "provider": candidate_settings.provider_type,
            "protocol": candidate_settings.openai_protocol,
            "model_name": candidate_settings.openai_model,
            "test_response_received": result["test_response_received"],
            "message": "A minimal generation request succeeded using the selected protocol and model.",
        }
    if candidate_key or clear_local:
        raise HTTPException(422, {"errors": {"api_key": "Credentials are available only for OpenAI Compatible."}})
    candidate_provider = OllamaProvider(candidate_settings)
    try:
        provider_version = candidate_provider.version()
        models = candidate_provider.models()
    except ProviderUnavailable as exc:
        # Do not return exception text from a provider client to the browser or
        # logs. It may include deployment-specific endpoint details.
        raise HTTPException(503, {"message": "Provider could not be reached. Check the base URL and service availability."}) from exc

    matching = next((item for item in models if item.get("name") == candidate_settings.ollama_model), None)
    if matching is None:
        return {
            "ok": False,
            "provider": candidate_settings.provider_type,
            "provider_reachable": True,
            "provider_version": provider_version.get("version"),
            "model_found": False,
            "message": "Provider is reachable, but the requested model tag is not installed.",
        }
    actual_digest = matching.get("digest")
    digest_match = not candidate_settings.ollama_model_digest or actual_digest == candidate_settings.ollama_model_digest
    if not digest_match:
        return {
            "ok": False,
            "provider": candidate_settings.provider_type,
            "provider_reachable": True,
            "provider_version": provider_version.get("version"),
            "model_name": matching.get("name"),
            "model_digest": actual_digest,
            "model_found": True,
            "digest_match": False,
            "message": "Provider is reachable, but the installed model digest does not match the configured pin.",
        }
    test_id = admin_config.record_provider_test(candidate, None)
    return {
        "ok": True,
        "test_id": test_id,
        "provider": candidate_settings.provider_type,
        "provider_reachable": True,
        "provider_version": provider_version.get("version"),
        "model_name": matching.get("name"),
        "model_digest": actual_digest,
        "model_found": True,
        "digest_match": digest_match,
        "message": "Provider and configured model identity verified.",
    }


@app.put("/admin/config")
def admin_save_config(body: dict[str, object], request: Request) -> dict[str, object]:
    allowed = {"config", "test_id", "api_key", "openai_api_key", "clear_credential"}
    if set(body) - allowed or not isinstance(body.get("config"), dict) or not isinstance(body.get("test_id"), str):
        raise HTTPException(422, {"errors": {"config": "Expected config values and a successful provider test ID."}})
    try:
        apply_state = admin_config.save(
            body["config"], body["test_id"],
            candidate_key=str(body.get("api_key") or body.get("openai_api_key") or ""),
            clear_local=body.get("clear_credential") is True,
        )
    except ConfigValidationError as exc:
        raise HTTPException(422, {"errors": exc.errors}) from exc
    state = _admin_state(request)
    state["apply_state"] = apply_state
    return state


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


@app.get("/providers/openai-compatible/status")
def openai_compatible_status() -> dict[str, object]:
    return {
        "provider": "openai_compatible",
        "protocol": settings.openai_protocol,
        "endpoint": settings.openai_base_url,
        "model": settings.openai_model,
        "credential_status": "CONFIGURED" if active_api_key else "MISSING",
        "status": "configured" if settings.provider_type == "openai_compatible" and active_api_key else "not-active-or-missing-credential",
    }


@app.post("/admin/provider/credential/clear")
def admin_clear_provider_credential(body: dict[str, object]) -> dict[str, object]:
    if body != {"confirm": True}:
        raise HTTPException(422, {"message": "Explicit confirmation is required to clear the locally saved credential."})
    admin_config.secret_store.clear()
    return {"credential_status": "CONFIGURED" if admin_config.secret_store.environment_key() else "MISSING",
            "credential_source": admin_config.secret_store.source(),
            "apply_state": "RESTART_REQUIRED" if settings.provider_type == "openai_compatible" else admin_config.apply_state()}


@app.post("/admin/restart", status_code=202)
async def admin_restart(request: Request, background_tasks: BackgroundTasks) -> dict[str, str]:
    if _restart_strategy() != "SUPERVISED_PROCESS_EXIT" or not _request_is_loopback(request):
        raise HTTPException(503, {"code": "RESTART_UNAVAILABLE", "message": "Safe supervised restart is unavailable for this service connection."})
    if _admin_state()["apply_state"] != "RESTART_REQUIRED":
        raise HTTPException(409, {"code": "NO_RESTART_REQUIRED", "message": "The saved configuration is already active."})
    restart_id = secrets.token_urlsafe(18)
    background_tasks.add_task(_terminate_after_response)
    return {"restart_id": restart_id, "state": "RESTARTING", "strategy": "SUPERVISED_PROCESS_EXIT"}


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
        publisher=body.publisher, raw_bytes=content_bytes,
        locator_ready=parsed.locator_ready, element_counts=parsed.element_counts,
    )
    return {"source_id": source_id, "source_revision": revision_id, "created": created,
            "publisher": (body.publisher or "").strip() or None,
            "source_sha256": hashlib.sha256(content_bytes).hexdigest(),
            "parser_snapshot": {"id": parsed.parser_id, "version": parsed.parser_version},
            "element_counts": parsed.element_counts or {}, "locator_status": "READY" if parsed.locator_ready else "PARTIAL_NOT_EVIDENCE_READY",
            "chunk_count": len(chunks)}


@app.post("/sources/import-file")
async def import_file(title: str = Form(..., min_length=1, max_length=300),
                      classification: str = Form(...), file: UploadFile = File(...),
                      publisher: str | None = Form(default=None, max_length=200),
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
        publisher=publisher, raw_bytes=content_bytes,
        locator_ready=parsed.locator_ready, element_counts=parsed.element_counts,
        original_filename=filename,
    )
    return {"source_id": source_id, "source_revision": revision_id, "created": created,
            "publisher": (publisher or "").strip() or None,
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


@app.delete("/sources/{source_id}/revisions/{revision_id}")
def delete_source_revision(source_id: str, revision_id: str) -> dict[str, object]:
    result = store.delete_revision(source_id, revision_id)
    if result is None:
        raise HTTPException(404, "Source revision not found.")
    return {
        **result,
        "scope": "PUBLIC_KNOWLEDGE_SOURCE_ONLY",
        "formal_knowledge_affected": False,
    }


@app.delete("/sources/{source_id}")
def delete_source(source_id: str) -> dict[str, object]:
    result = store.delete_source(source_id)
    if result is None:
        raise HTTPException(404, "Source not found.")
    return {
        **result,
        "scope": "PUBLIC_KNOWLEDGE_SOURCE_ONLY",
        "formal_knowledge_affected": False,
    }


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

    generation_question = body.question
    if body.response_language == "zh-CN":
        generation_question = (
            "请使用简体中文回答。技术术语可保留英文括注；严格只依据提供的公开资料，"
            "保留 [1]、[2] 这类引用标记，不使用外部知识。"
            + (
                "先给出中文综合解读，再按引用编号逐条给出对应原文片段的忠实中文翻译；"
                "翻译不得增补原文没有的事实。"
                if body.include_citation_translations else
                ""
            )
            + "\n\n原问题：" + body.question
        )
    elif body.response_language == "en":
        generation_question = (
            "Answer in English using only the supplied public-source excerpts; "
            "preserve citation markers and do not use outside knowledge.\n\nQuestion: "
            + body.question
        )

    try:
        answer, model_snapshot = active_generation_provider().generate(generation_question, hits)
    except ProviderUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    citations = [{"citation_id": h.hit_id, "source_id": h.source_id, "source_revision": h.source_revision,
                  "locator": h.locator, "text": h.text} for h in hits]
    return {"answer": answer, "citations": citations,
            "source_refs": [{"source_id": h.source_id, "source_revision": h.source_revision} for h in hits],
            "model_snapshot": model_snapshot,
            "response_language": body.response_language,
            "citation_translations_requested": body.include_citation_translations,
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
