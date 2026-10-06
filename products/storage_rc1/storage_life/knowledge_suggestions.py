"""Wave 2 Public Knowledge suggestions and existing-KP handoff adapter.

Suggestions are workspace drafts. Formal candidates are always created through
the shared Knowledge Production intake and repository.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Callable, Literal
from urllib.parse import urlparse
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class SuggestionError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class SourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    locator: dict[str, Any]
    citation_id: str = Field(min_length=1)
    source_uri: str | None = None
    immutable_identity: str | None = None


class PublicKnowledgeSuggestionV1(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract_version: Literal["public-knowledge-suggestion/v1"] = "public-knowledge-suggestion/v1"
    suggestion_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=300)
    suggested_object_type: str = Field(default="FACT", min_length=1)
    summary: str = ""
    content: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[SourceRef] = Field(min_length=1)
    origin_workspace: str = "public-knowledge"
    origin_mode: str
    origin_kind: str = "SEARCH"
    origin_query: str = ""
    generated_by: dict[str, str] = Field(default_factory=lambda: {"service": "public-knowledge", "mode": "rag-assisted"})
    content_origin: str = "USER_CURATED_FROM_SELECTED_CITATIONS"
    status: str = "DRAFT"
    created_at: str
    updated_at: str
    candidate_id: str | None = None
    handoff_evidence_status: str | None = None
    handoff_error: str | None = None


class SuggestionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=300)
    suggested_object_type: str = "FACT"
    summary: str = ""
    content: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[SourceRef] = Field(min_length=1)
    origin_mode: str
    origin_kind: str = "SEARCH"
    origin_query: str = ""


class SuggestionEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=300)
    suggested_object_type: str = "FACT"
    summary: str = ""
    content: dict[str, Any] = Field(default_factory=dict)


def _canon(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _safe_source_uri(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if len(raw) > 2048:
        raise SuggestionError("SOURCE_URI_INVALID")
    try:
        parsed = urlparse(raw)
        port = parsed.port
    except ValueError as exc:
        raise SuggestionError("SOURCE_URI_INVALID") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise SuggestionError("SOURCE_URI_INVALID")
    host = parsed.hostname.lower()
    host_text = f"[{host}]" if ":" in host and not host.startswith("[") else host
    authority = host_text + (f":{port}" if port is not None else "")
    query = f"?{parsed.query}" if parsed.query else ""
    fragment = f"#{parsed.fragment}" if parsed.fragment else ""
    return f"{parsed.scheme.lower()}://{authority}{parsed.path or ''}{query}{fragment}"

class PublicKnowledgeSuggestionService:
    def __init__(self, repository, *, evidence_intake, candidate_intake,
                 resolve_citation: Callable[[str, str], dict[str, Any]],
                 resolve_source: Callable[[str, str], dict[str, Any]]):
        self.repository = repository
        self.evidence_intake = evidence_intake
        self.candidate_intake = candidate_intake
        self.resolve_citation = resolve_citation
        self.resolve_source = resolve_source

    def _path(self, suggestion_id: str) -> str:
        return f"public_knowledge/suggestions/{suggestion_id}.json"

    def list(self) -> list[dict[str, Any]]:
        result = []
        for path in self.repository.list("public_knowledge/suggestions"):
            payload = self.repository.load(path)
            if isinstance(payload, dict):
                try:
                    result.append(PublicKnowledgeSuggestionV1.model_validate(payload).model_dump(mode="json"))
                except ValidationError:
                    continue
        return sorted(result, key=lambda item: item["created_at"], reverse=True)

    def get(self, suggestion_id: str) -> dict[str, Any]:
        payload = self.repository.load(self._path(suggestion_id))
        if not isinstance(payload, dict):
            raise SuggestionError("SUGGESTION_NOT_FOUND")
        try:
            return PublicKnowledgeSuggestionV1.model_validate(payload).model_dump(mode="json")
        except ValidationError as exc:
            raise SuggestionError("SUGGESTION_CONTRACT_INVALID") from exc

    def create(self, payload: SuggestionCreate | dict[str, Any]) -> dict[str, Any]:
        try:
            request = payload if isinstance(payload, SuggestionCreate) else SuggestionCreate.model_validate(payload)
        except ValidationError as exc:
            raise SuggestionError("SUGGESTION_CONTRACT_INVALID") from exc
        if request.origin_mode == "FIXTURE_REPLAY":
            state = "DEMO_ONLY"
        elif request.origin_mode == "LIVE":
            state = "DRAFT"
        else:
            raise SuggestionError("SUGGESTION_MODE_INVALID")
        now = datetime.now(timezone.utc).isoformat()
        suggestion = PublicKnowledgeSuggestionV1(
            suggestion_id="PKS-" + uuid4().hex,
            title=request.title,
            suggested_object_type=request.suggested_object_type,
            summary=request.summary,
            content=request.content,
            source_refs=request.source_refs,
            origin_mode=request.origin_mode,
            origin_kind=request.origin_kind,
            origin_query=request.origin_query,
            content_origin=(
                "RAG_ASSISTED_DRAFT"
                if request.origin_kind.upper() == "QA"
                else "USER_CURATED_FROM_SELECTED_CITATIONS"
            ),
            status=state,
            created_at=now,
            updated_at=now,
        )
        stored = suggestion.model_dump(mode="json")
        self.repository.save(self._path(suggestion.suggestion_id), stored)
        return stored

    def edit(self, suggestion_id: str, payload: SuggestionEdit | dict[str, Any]) -> dict[str, Any]:
        current = self.get(suggestion_id)
        editable_states = {
            "DRAFT", "DEMO_ONLY", "READY_FOR_HANDOFF", "EVIDENCE_UNRESOLVED",
            "REVISION_MISMATCH", "SOURCE_UNAVAILABLE", "HANDOFF_FAILED",
        }
        if current["status"] not in editable_states:
            raise SuggestionError("SUGGESTION_STATE_INVALID")
        try:
            edit = payload if isinstance(payload, SuggestionEdit) else SuggestionEdit.model_validate(payload)
        except ValidationError as exc:
            raise SuggestionError("SUGGESTION_CONTRACT_INVALID") from exc
        updated = {**current, **edit.model_dump(), "updated_at": datetime.now(timezone.utc).isoformat()}
        if updated["status"] not in {"DRAFT", "DEMO_ONLY"}:
            updated["status"] = "DRAFT"
        self.repository.save(self._path(suggestion_id), updated)
        return updated

    def delete(self, suggestion_id: str) -> None:
        current = self.get(suggestion_id)
        if current["status"] in {"HANDED_OFF", "ACCEPTED_AS_CANDIDATE"}:
            raise SuggestionError("HANDED_OFF_SUGGESTION_IMMUTABLE")
        self.repository.resolve(self._path(suggestion_id)).unlink(missing_ok=True)

    def validate(self, suggestion_id: str, *, mode: str) -> dict[str, Any]:
        suggestion = self.get(suggestion_id)
        if suggestion["status"] in {"HANDED_OFF", "ACCEPTED_AS_CANDIDATE"}:
            raise SuggestionError("SUGGESTION_STATE_INVALID")
        if mode != "LIVE" or suggestion["origin_mode"] != "LIVE":
            suggestion["status"] = "DEMO_ONLY" if suggestion["origin_mode"] == "FIXTURE_REPLAY" else suggestion["status"]
            suggestion["handoff_error"] = "DEMO_ONLY_BLOCKED"
            suggestion["updated_at"] = datetime.now(timezone.utc).isoformat()
            self.repository.save(self._path(suggestion_id), suggestion)
            raise SuggestionError("DEMO_ONLY_BLOCKED")
        if not suggestion["source_refs"]:
            raise SuggestionError("SOURCE_REFERENCE_REQUIRED")
        content = suggestion.get("content") or {}
        if not any(str(content.get(key) or "").strip() for key in ("definition", "engineering_meaning", "applicability")):
            raise SuggestionError("SUGGESTION_CONTENT_REQUIRED")
        try:
            for ref in suggestion["source_refs"]:
                self._resolve_ref(ref, "LIVE")
        except SuggestionError as exc:
            self._mark_failure(suggestion, exc.code)
            raise
        suggestion["status"] = "READY_FOR_HANDOFF"
        suggestion["handoff_evidence_status"] = "RESOLVED"
        suggestion["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.repository.save(self._path(suggestion_id), suggestion)
        return suggestion

    def handoff(self, suggestion_id: str, *, mode: str) -> dict[str, Any]:
        suggestion = self.get(suggestion_id)
        if suggestion["status"] in {"HANDED_OFF", "ACCEPTED_AS_CANDIDATE"} and suggestion.get("candidate_id"):
            return self._handoff_result(suggestion)
        if mode != "LIVE" or suggestion["origin_mode"] != "LIVE":
            suggestion["handoff_error"] = "DEMO_ONLY_BLOCKED"
            self.repository.save(self._path(suggestion_id), suggestion)
            raise SuggestionError("DEMO_ONLY_BLOCKED")
        if suggestion["status"] not in {"READY_FOR_HANDOFF", "HANDED_OFF"}:
            raise SuggestionError("SUGGESTION_NOT_READY_FOR_HANDOFF")

        evidence_ids: list[str] = []
        source_refs: list[str] = []
        for ref in suggestion["source_refs"]:
            try:
                citation, source = self._resolve_ref(ref, "LIVE")
            except SuggestionError as exc:
                self._mark_failure(suggestion, exc.code)
                raise
            source_id = ref["source_id"]
            revision = ref["revision"]
            text = citation.get("text")
            if not isinstance(text, str) or not text.strip():
                self._mark_failure(suggestion, "EVIDENCE_TEXT_UNAVAILABLE")
                raise SuggestionError("EVIDENCE_TEXT_UNAVAILABLE")
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            evidence_id = "PK-EVD-" + hashlib.sha256(
                (source_id + "|" + revision + "|" + ref["citation_id"] + "|" + _canon(ref["locator"])).encode("utf-8")
            ).hexdigest()[:24]
            try:
                self.evidence_intake.intake({
                    "evidence_id": evidence_id,
                    "source_document_id": source_id,
                    "domain": "OTHER",
                    "source_type": str(source.get("media_type") or "PUBLIC_SOURCE"),
                    "source_ref": str(
                        ref.get("source_uri")
                        or source.get("source_uri")
                        or source.get("official_url")
                        or f"public-knowledge://sources/{source_id}"
                    ),
                    "source_revision": revision,
                    "page": ref["locator"].get("page"),
                    "section": ref["locator"].get("section"),
                    "paragraph": ref["locator"].get("chunk_id") or ref["locator"].get("paragraph"),
                    "source_text": text,
                    "content_hash": digest,
                    "metadata": {
                        "citation_id": ref["citation_id"],
                        "locator": ref["locator"],
                        "classification": "PUBLIC",
                        "source_uri": ref.get("source_uri") or source.get("source_uri") or source.get("official_url"),
                        "immutable_identity": ref.get("immutable_identity"),
                        "origin_workspace": suggestion["origin_workspace"],
                        "origin_mode": "LIVE",
                    },
                })
            except Exception as exc:
                code = getattr(exc, "code", "HANDOFF_FAILED")
                self._mark_failure(suggestion, "HANDOFF_FAILED", preserve_code=code)
                raise SuggestionError(code) from exc
            evidence_ids.append(evidence_id)
            source_refs.append(f"OTHER:{source_id}@{revision}")

        suggestion["status"] = "HANDED_OFF"
        suggestion["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.repository.save(self._path(suggestion_id), suggestion)
        content = {**suggestion["content"], "source_evidence": [
            {"source_id": ref["source_id"], "revision": ref["revision"], "locator": ref["locator"], "citation_id": ref["citation_id"]}
            for ref in suggestion["source_refs"]
        ]}
        candidate_id = "PKC-" + hashlib.sha256(suggestion_id.encode("utf-8")).hexdigest()[:24]
        try:
            candidate = self.candidate_intake.intake({
                "candidate_id": candidate_id,
                "candidate_source_type": "BUSINESS",
                "business_source_type": "OTHER",
                "business_source_id": suggestion_id,
                "business_source_version": "v1",
                "object_type": suggestion["suggested_object_type"],
                "title": suggestion["title"],
                "summary": suggestion["summary"] or None,
                "content": _canon(content),
                "scope": ["PUBLIC_KNOWLEDGE"],
                "conditions": [],
                "limitations": self._as_list(suggestion["content"].get("limitations")),
                "tags": ["public-knowledge", "rag-assisted"],
                "source_refs": source_refs,
                "evidence_refs": evidence_ids,
                "confidence": None,
                "created_at": suggestion["created_at"],
                "producer": "PUBLIC_KNOWLEDGE_HANDOFF",
                "contract_version": "knowledge-candidate/v1",
                "metadata": {
                    "origin": "public-knowledge/rag-assisted",
                    "origin_workspace": suggestion["origin_workspace"],
                    "origin_mode": "LIVE",
                    "suggestion_id": suggestion_id,
                    "source_refs": suggestion["source_refs"],
                    "ai_content_is_not_evidence": True,
                    "generated_by": suggestion["generated_by"],
                    "evidence_status": "RESOLVED",
                },
            })
        except Exception as exc:
            code = getattr(exc, "code", "CANDIDATE_INTAKE_FAILED")
            self._mark_failure(suggestion, "HANDOFF_FAILED", preserve_code=code)
            raise SuggestionError(code) from exc
        suggestion["status"] = "ACCEPTED_AS_CANDIDATE"
        suggestion["candidate_id"] = candidate.candidate_id
        suggestion["handoff_evidence_status"] = "RESOLVED"
        suggestion["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.repository.save(self._path(suggestion_id), suggestion)
        return self._handoff_result(suggestion)

    @staticmethod
    def _as_list(value: Any) -> list[str]:
        if value is None or value == "":
            return []
        if isinstance(value, list):
            return [str(item) for item in value if item is not None and str(item).strip()]
        return [str(value)]

    def _mark_failure(self, suggestion: dict[str, Any], code: str, *, preserve_code: str | None = None) -> None:
        status = {
            "CITATION_UNRESOLVED": "EVIDENCE_UNRESOLVED",
            "EVIDENCE_TEXT_UNAVAILABLE": "EVIDENCE_UNRESOLVED",
            "SOURCE_LOCATOR_REQUIRED": "EVIDENCE_UNRESOLVED",
            "SOURCE_LOCATOR_MISMATCH": "EVIDENCE_UNRESOLVED",
            "SOURCE_ID_MISMATCH": "EVIDENCE_UNRESOLVED",
            "SOURCE_IDENTITY_MISMATCH": "EVIDENCE_UNRESOLVED",
            "SOURCE_REVISION_MISMATCH": "REVISION_MISMATCH",
            "SOURCE_UNAVAILABLE": "SOURCE_UNAVAILABLE",
            "SOURCE_NOT_PUBLIC": "HANDOFF_FAILED",
            "DEMO_ONLY_BLOCKED": "DEMO_ONLY",
            "HANDOFF_FAILED": "HANDOFF_FAILED",
        }.get(code, "HANDOFF_FAILED")
        suggestion["status"] = status
        suggestion["handoff_error"] = preserve_code or code
        suggestion["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.repository.save(self._path(suggestion["suggestion_id"]), suggestion)

    def _resolve_ref(self, ref: dict[str, Any], mode: str) -> tuple[dict[str, Any], dict[str, Any]]:
        citation_id = ref.get("citation_id")
        try:
            citation = self.resolve_citation(str(citation_id), mode)
        except Exception as exc:
            raise SuggestionError("CITATION_UNRESOLVED") from exc
        if not isinstance(citation, dict):
            raise SuggestionError("CITATION_UNRESOLVED")
        if citation.get("mode") != "LIVE":
            raise SuggestionError("NON_LIVE_EVIDENCE")
        if citation.get("source_id") != ref.get("source_id"):
            raise SuggestionError("SOURCE_ID_MISMATCH")
        if citation.get("source_revision") != ref.get("revision"):
            raise SuggestionError("SOURCE_REVISION_MISMATCH")
        if not isinstance(ref.get("locator"), dict) or not ref["locator"]:
            raise SuggestionError("SOURCE_LOCATOR_REQUIRED")
        if not isinstance(citation.get("locator"), dict) or _canon(citation["locator"]) != _canon(ref.get("locator")):
            raise SuggestionError("SOURCE_LOCATOR_MISMATCH")
        try:
            source_response = self.resolve_source(str(ref["source_id"]), "LIVE")
        except Exception as exc:
            raise SuggestionError("SOURCE_UNAVAILABLE") from exc
        if not isinstance(source_response, dict):
            raise SuggestionError("SOURCE_UNAVAILABLE")
        source = source_response.get("source") if isinstance(source_response.get("source"), dict) else source_response
        if not isinstance(source, dict):
            raise SuggestionError("SOURCE_UNAVAILABLE")
        if source.get("source_id") != ref.get("source_id"):
            raise SuggestionError("SOURCE_ID_MISMATCH")
        # The Public Knowledge RAG Source API exposes this contract as
        # ``source_class``. Accept ``classification`` as well for existing
        # compatible providers and captured fixtures.
        classification = source.get("classification") or source.get("source_class")
        if str(classification or "").upper() != "PUBLIC":
            raise SuggestionError("SOURCE_NOT_PUBLIC")
        resolved_uri = source.get("source_uri") or source.get("official_url")
        if ref.get("source_uri") and resolved_uri and ref["source_uri"] != resolved_uri:
            raise SuggestionError("SOURCE_IDENTITY_MISMATCH")
        source_status = str(source.get("status") or source.get("source_status") or "ACTIVE").upper()
        if source_status not in {"ACTIVE", "AVAILABLE", "PUBLISHED"}:
            raise SuggestionError("SOURCE_UNAVAILABLE")
        revisions = source_response.get("revisions") or []
        valid_revisions = {str(row.get("revision_id") or row.get("source_revision") or row.get("version") or "") for row in revisions if isinstance(row, dict)}
        current_revision = str(source.get("revision_id") or source.get("revision") or source.get("version") or "")
        if ref.get("revision") not in valid_revisions and ref.get("revision") != current_revision:
            raise SuggestionError("SOURCE_REVISION_MISMATCH")
        if ref.get("immutable_identity"):
            matching_revision = next(
                (
                    row for row in revisions
                    if isinstance(row, dict)
                    and str(
                        row.get("revision_id")
                        or row.get("source_revision")
                        or row.get("version")
                        or ""
                    ) == str(ref.get("revision") or "")
                ),
                None,
            )
            actual_identity = (
                (matching_revision or {}).get("raw_sha256")
                or (matching_revision or {}).get("content_sha256")
                or source.get("immutable_identity")
                or source.get("content_hash")
            )
            if not actual_identity or actual_identity != ref["immutable_identity"]:
                raise SuggestionError("SOURCE_IDENTITY_MISMATCH")
        return citation, source

    @staticmethod
    def _handoff_result(suggestion: dict[str, Any]) -> dict[str, Any]:
        return {
            "suggestion_id": suggestion["suggestion_id"],
            "status": suggestion["status"],
            "candidate_id": suggestion.get("candidate_id"),
            "evidence_status": suggestion.get("handoff_evidence_status"),
            "candidate_url": f"/knowledge-production/candidates/{suggestion.get('candidate_id')}" if suggestion.get("candidate_id") else None,
        }
