"""Thin Hardware Case -> Unified Knowledge public-contract adapter.

This module intentionally has no dependency on Knowledge repositories, DB
models, Runtime or Provider internals.  It only emits/consumes the frozen
knowledge-*/v1 HTTP contracts delivered by KNOWLEDGE_CAPABILITY_RELEASE_V0.3.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


KNOWLEDGE_CAPABILITY_VERSION = "KNOWLEDGE_CAPABILITY_RELEASE_V0.3"
KNOWLEDGE_SOURCE_COMMIT = "92ef3f3c4ec80c4987f2b715dcd2d485eda8e372"
KNOWLEDGE_MAIN_COMMIT = "1ed6dc186637049afaa98c28ea2c8042c63588ae"
KNOWLEDGE_SHA256 = (
    "2e73cf0cf10a92a33378f72546e76204918ff756c1afc466d4de017c67933550"
)

CONTRACT_VERSIONS = {
    "candidate": "knowledge-candidate/v1",
    "evidence": "knowledge-evidence/v1",
    "review": "knowledge-review/v1",
    "publish": "knowledge-publish/v1",
    "object": "knowledge-object/v1",
    "query": "knowledge-query/v1",
}

DOMAIN = "HARDWARE_CASE"
OBJECT_TYPE = "HARDWARE_CASE"


class HardwareKnowledgeTransport(Protocol):
    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, Any] | None = None,
        query: Mapping[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        """Return HTTP-like status code plus decoded JSON object."""


class HardwareKnowledgeAdapterError(RuntimeError):
    def __init__(self, code: str, *, status_code: int | None = None):
        self.code = code
        self.status_code = status_code
        super().__init__(code)


@dataclass(frozen=True)
class KnowledgeReleaseDescriptor:
    capability_version: str = KNOWLEDGE_CAPABILITY_VERSION
    capability_source_commit: str = KNOWLEDGE_SOURCE_COMMIT
    knowledge_main_commit: str = KNOWLEDGE_MAIN_COMMIT
    capability_sha256: str = KNOWLEDGE_SHA256
    contract_versions: Mapping[str, str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.contract_versions is None:
            object.__setattr__(self, "contract_versions", dict(CONTRACT_VERSIONS))


class KnowledgeHttpTransport:
    """Minimal JSON-over-HTTP transport for the public Knowledge API."""

    def __init__(self, base_url: str, *, timeout_seconds: float = 20.0):
        base = str(base_url or "").strip().rstrip("/")
        if not base.startswith(("http://", "https://")):
            raise HardwareKnowledgeAdapterError("KNOWLEDGE_BASE_URL_INVALID")
        self.base_url = base
        self.timeout_seconds = float(timeout_seconds)

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, Any] | None = None,
        query: Mapping[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        url = self.base_url + path
        if query:
            url += "?" + urlencode(
                [(str(key), str(value)) for key, value in query.items()]
            )
        payload = None
        headers = {"Accept": "application/json"}
        if json_body is not None:
            payload = json.dumps(
                dict(json_body),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = Request(
            url,
            data=payload,
            method=method.upper(),
            headers=headers,
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read()
                return int(response.status), self._decode(body)
        except HTTPError as error:
            return int(error.code), self._decode(error.read())
        except (URLError, TimeoutError, OSError) as error:
            raise HardwareKnowledgeAdapterError(
                "KNOWLEDGE_UNAVAILABLE"
            ) from error

    @staticmethod
    def _decode(payload: bytes) -> dict[str, Any]:
        try:
            value = json.loads(payload.decode("utf-8"))
        except Exception as error:
            raise HardwareKnowledgeAdapterError(
                "KNOWLEDGE_RESPONSE_INVALID"
            ) from error
        if not isinstance(value, dict):
            raise HardwareKnowledgeAdapterError("KNOWLEDGE_RESPONSE_INVALID")
        return value


class HardwareCaseKnowledgeAdapter:
    """Map Hardware-owned state to frozen Knowledge public contracts only."""

    def __init__(
        self,
        transport: HardwareKnowledgeTransport,
        *,
        knowledge_release_version: str,
        descriptor: KnowledgeReleaseDescriptor | None = None,
    ) -> None:
        release = str(knowledge_release_version or "").strip()
        if not release:
            raise HardwareKnowledgeAdapterError(
                "KNOWLEDGE_RELEASE_VERSION_REQUIRED"
            )
        self.transport = transport
        self.knowledge_release_version = release
        self.descriptor = descriptor or KnowledgeReleaseDescriptor()

    def intake_evidence(
        self,
        *,
        case_id: str,
        source_metadata: Mapping[str, Any],
        evidence: Mapping[str, Any],
        revision: int,
        source_revision: str | None = None,
    ) -> dict[str, Any]:
        if int(revision) < 1:
            raise HardwareKnowledgeAdapterError("REVISION_INVALID")
        source_document_id = str(
            source_metadata.get("source_id") or ""
        ).strip()
        source_ref = str(evidence.get("source_ref") or "").strip()
        evidence_id = str(evidence.get("evidence_id") or "").strip()
        mime_type = str(source_metadata.get("mime_type") or "").lower()
        source_type = str(
            source_metadata.get("source_type")
            or ("WORD" if "wordprocessingml" in mime_type else "DOCUMENT")
        ).strip()
        locator = evidence.get("locator")
        if (
            not case_id
            or not source_document_id
            or not source_ref
            or not evidence_id
            or not isinstance(locator, Mapping)
        ):
            raise HardwareKnowledgeAdapterError("EVIDENCE_CONTRACT_INVALID")

        excerpt = evidence.get("excerpt_or_caption")
        source_text = None if excerpt is None else str(excerpt)
        if source_text is not None:
            content_hash = hashlib.sha256(
                source_text.encode("utf-8")
            ).hexdigest()
        else:
            content_hash = str(source_metadata.get("sha256") or "").strip()
        if len(content_hash) != 64:
            raise HardwareKnowledgeAdapterError("EVIDENCE_CONTRACT_INVALID")

        payload = {
            "contract_version": CONTRACT_VERSIONS["evidence"],
            "evidence_id": evidence_id,
            "source_document_id": source_document_id,
            "domain": DOMAIN,
            "source_type": source_type,
            "source_ref": source_ref,
            "source_revision": source_revision or f"R{revision}",
            "page": self._positive_int(locator.get("page")),
            "section": self._optional_text(
                locator.get("section")
                or self._section_from_path(locator.get("section_path"))
            ),
            "paragraph": self._optional_text(locator.get("paragraph")),
            "source_text": source_text,
            "content_hash": content_hash,
            "revision": int(revision),
            "metadata": {
                "hardware_case_id": case_id,
                "hardware_evidence_id": evidence_id,
                "hardware_evidence_type": evidence.get("evidence_type"),
                "hardware_locator": dict(locator),
                "hardware_source_sha256": source_metadata.get("sha256"),
                "knowledge_capability_version": (
                    self.descriptor.capability_version
                ),
            },
        }
        return self._request(
            "POST",
            "/v1/knowledge/evidences",
            json_body=payload,
        )

    def intake_candidate(
        self,
        *,
        case_id: str,
        source_document_id: str,
        source_ref: str,
        structured_content: Mapping[str, Any],
        evidence_refs: Sequence[str],
        revision: int,
        source_version: str | None = None,
        object_type: str = OBJECT_TYPE,
    ) -> dict[str, Any]:
        if (
            not str(case_id or "").strip()
            or not str(source_document_id or "").strip()
            or not str(source_ref or "").strip()
            or not isinstance(structured_content, Mapping)
            or int(revision) < 1
        ):
            raise HardwareKnowledgeAdapterError("CANDIDATE_CONTRACT_INVALID")
        refs = [str(item).strip() for item in evidence_refs if str(item).strip()]
        if len(refs) != len(set(refs)):
            raise HardwareKnowledgeAdapterError("CANDIDATE_CONTRACT_INVALID")

        payload = {
            "contract_version": CONTRACT_VERSIONS["candidate"],
            "candidate_id": self.candidate_id(case_id, revision),
            "source_document_id": str(source_document_id),
            "domain": DOMAIN,
            "object_type": str(object_type or OBJECT_TYPE),
            "structured_content": dict(structured_content),
            "evidence_refs": refs,
            "status": "PENDING_REVIEW",
            "created_at": datetime.utcnow().isoformat() + "Z",
            "revision": int(revision),
            "source_version": source_version or f"R{revision}",
            "producer": "hardware-case/v1",
            "metadata": {
                "hardware_case_id": case_id,
                "hardware_source_ref": source_ref,
                "knowledge_capability_version": (
                    self.descriptor.capability_version
                ),
                "knowledge_capability_sha256": (
                    self.descriptor.capability_sha256
                ),
            },
        }
        response = self._request(
            "POST",
            "/v1/knowledge/candidates",
            json_body=payload,
        )
        self._require_contract_field(
            response,
            "contract_version",
            CONTRACT_VERSIONS["candidate"],
        )
        if response.get("domain") != DOMAIN:
            raise HardwareKnowledgeAdapterError(
                "KNOWLEDGE_DOMAIN_MISMATCH"
            )
        if response.get("object_type") != str(object_type or OBJECT_TYPE):
            raise HardwareKnowledgeAdapterError(
                "KNOWLEDGE_OBJECT_TYPE_MISMATCH"
            )
        if response.get("structured_content") != dict(structured_content):
            raise HardwareKnowledgeAdapterError(
                "KNOWLEDGE_CONTENT_MISMATCH"
            )
        if response.get("evidence_refs") != refs:
            raise HardwareKnowledgeAdapterError(
                "KNOWLEDGE_EVIDENCE_REFS_MISMATCH"
            )
        if int(response.get("revision") or 0) != int(revision):
            raise HardwareKnowledgeAdapterError("KNOWLEDGE_REVISION_MISMATCH")
        return response

    def review_candidate(
        self,
        *,
        candidate_id: str,
        state: str,
        reviewer: str,
        review_time: datetime,
        revision: int,
        review_comment: str | None = None,
        confirmed_content: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized = str(state or "").strip().upper()
        if normalized == "PENDING_REVIEW":
            return {
                "contract_version": CONTRACT_VERSIONS["review"],
                "candidate_id": candidate_id,
                "review_status": "PENDING_REVIEW",
                "revision": int(revision),
            }
        if normalized not in {"CONFIRMED", "REJECTED"}:
            raise HardwareKnowledgeAdapterError("REVIEW_CONTRACT_INVALID")
        if not candidate_id or not reviewer or int(revision) < 1:
            raise HardwareKnowledgeAdapterError("REVIEW_CONTRACT_INVALID")

        payload: dict[str, Any] = {
            "contract_version": CONTRACT_VERSIONS["review"],
            "candidate_id": candidate_id,
            "action": "CONFIRM" if normalized == "CONFIRMED" else "REJECT",
            "reviewer": reviewer,
            "review_time": review_time.isoformat(),
            "review_comment": review_comment,
            "revision": int(revision),
        }
        if normalized == "CONFIRMED":
            if not isinstance(confirmed_content, Mapping):
                raise HardwareKnowledgeAdapterError(
                    "REVIEW_CONTRACT_INVALID"
                )
            payload["confirmed_value"] = dict(confirmed_content)

        response = self._request(
            "POST",
            "/v1/knowledge/reviews",
            json_body=payload,
        )
        self._require_contract_field(
            response,
            "contract_version",
            CONTRACT_VERSIONS["review"],
        )
        expected = "CONFIRMED" if normalized == "CONFIRMED" else "REJECTED"
        if response.get("review_status") != expected:
            raise HardwareKnowledgeAdapterError(
                "KNOWLEDGE_REVIEW_STATUS_MISMATCH"
            )
        return response

    def publish(
        self,
        *,
        candidate_id: str,
        hardware_publish_gate: Mapping[str, Any],
        evidence_refs: Sequence[str],
        publisher: str,
        published_at: datetime,
        revision: int,
    ) -> dict[str, Any]:
        if not bool(hardware_publish_gate.get("passed")):
            raise HardwareKnowledgeAdapterError(
                "HARDWARE_PUBLISH_GATE_NOT_PASSED"
            )
        refs = [str(item).strip() for item in evidence_refs if str(item).strip()]
        if not refs:
            raise HardwareKnowledgeAdapterError("EVIDENCE_MISSING")
        if not candidate_id or not publisher or int(revision) < 1:
            raise HardwareKnowledgeAdapterError("PUBLISH_CONTRACT_INVALID")

        payload = {
            "contract_version": CONTRACT_VERSIONS["publish"],
            "candidate_id": candidate_id,
            "idempotency_key": self.publish_idempotency_key(
                candidate_id, revision
            ),
            "publisher": publisher,
            "published_at": published_at.isoformat(),
            "revision": int(revision),
        }
        response = self._request(
            "POST",
            "/v1/knowledge/publish",
            json_body=payload,
        )
        self._require_contract_field(
            response,
            "contract_version",
            CONTRACT_VERSIONS["publish"],
        )
        obj = response.get("object")
        if not isinstance(obj, dict):
            raise HardwareKnowledgeAdapterError(
                "KNOWLEDGE_RESPONSE_INVALID"
            )
        self._validate_object(obj, expected_revision=revision)
        if obj.get("candidate_ref") != candidate_id:
            raise HardwareKnowledgeAdapterError(
                "KNOWLEDGE_PUBLIC_REF_MISMATCH"
            )
        if obj.get("evidence_refs") != refs:
            raise HardwareKnowledgeAdapterError(
                "KNOWLEDGE_EVIDENCE_REFS_MISMATCH"
            )
        return response

    def search(
        self,
        *,
        text: str | None = None,
        knowledge_ids: Sequence[str] = (),
    ) -> dict[str, Any]:
        payload = {
            "contract_version": CONTRACT_VERSIONS["query"],
            "knowledge_release_version": self.knowledge_release_version,
            "knowledge_ids": [
                str(item).strip()
                for item in knowledge_ids
                if str(item).strip()
            ],
            "domain": DOMAIN,
            "object_type": OBJECT_TYPE,
            "text": self._optional_text(text),
        }
        response = self._request(
            "POST",
            "/v1/knowledge/search",
            json_body=payload,
        )
        self._require_contract_field(
            response,
            "contract_version",
            CONTRACT_VERSIONS["query"],
        )
        if response.get("knowledge_release_version") != self.knowledge_release_version:
            raise HardwareKnowledgeAdapterError(
                "KNOWLEDGE_RELEASE_MISMATCH"
            )
        objects = response.get("objects")
        if not isinstance(objects, list):
            raise HardwareKnowledgeAdapterError(
                "KNOWLEDGE_RESPONSE_INVALID"
            )
        for obj in objects:
            if not isinstance(obj, dict):
                raise HardwareKnowledgeAdapterError(
                    "KNOWLEDGE_RESPONSE_INVALID"
                )
            self._validate_object(obj)
        return response

    def get_object(
        self,
        knowledge_id: str,
        *,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        if not knowledge_id:
            raise HardwareKnowledgeAdapterError(
                "KNOWLEDGE_OBJECT_NOT_FOUND"
            )
        path = "/v1/knowledge/objects/" + quote(knowledge_id, safe="")
        response = self._request(
            "GET",
            path,
            query={
                "knowledge_release_version": self.knowledge_release_version,
            },
        )
        self._validate_object(
            response,
            expected_revision=expected_revision,
        )
        return response

    def resolve_publication(
        self,
        public_ref: str,
        *,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Resolve a versioned Hardware Public Ref through the public contract."""
        ref = str(public_ref or "").strip()
        if not ref:
            raise HardwareKnowledgeAdapterError(
                "KNOWLEDGE_PUBLIC_REF_NOT_FOUND"
            )
        result = self._request(
            "POST",
            "/v1/knowledge/search",
            json_body={
                "contract_version": CONTRACT_VERSIONS["query"],
                "knowledge_release_version": self.knowledge_release_version,
                "candidate_refs": [ref],
                "domain": DOMAIN,
                "object_type": OBJECT_TYPE,
            },
        )
        self._require_contract_field(
            result,
            "contract_version",
            CONTRACT_VERSIONS["query"],
        )
        if result.get("knowledge_release_version") != self.knowledge_release_version:
            raise HardwareKnowledgeAdapterError(
                "KNOWLEDGE_RELEASE_MISMATCH"
            )
        objects = result.get("objects")
        if not isinstance(objects, list):
            raise HardwareKnowledgeAdapterError("KNOWLEDGE_RESPONSE_INVALID")
        matches = [
            obj for obj in objects
            if isinstance(obj, dict) and obj.get("candidate_ref") == ref
        ]
        if not matches:
            raise HardwareKnowledgeAdapterError(
                "KNOWLEDGE_PUBLIC_REF_NOT_FOUND"
            )
        if len(matches) != 1:
            raise HardwareKnowledgeAdapterError(
                "KNOWLEDGE_PUBLIC_REF_AMBIGUOUS"
            )
        obj = matches[0]
        self._validate_object(obj, expected_revision=expected_revision)
        if not isinstance(obj.get("candidate_ref"), str):
            raise HardwareKnowledgeAdapterError(
                "KNOWLEDGE_PUBLIC_REF_INVALID"
            )
        if not obj.get("evidence_refs"):
            raise HardwareKnowledgeAdapterError("EVIDENCE_MISSING")
        return obj

    def resolve_evidence(self, evidence_id: str) -> dict[str, Any]:
        if not evidence_id:
            raise HardwareKnowledgeAdapterError("EVIDENCE_NOT_FOUND")
        path = "/v1/knowledge/evidences/" + quote(evidence_id, safe="")
        response = self._request(
            "GET",
            path,
            query={
                "knowledge_release_version": self.knowledge_release_version,
            },
        )
        if response.get("evidence_id") != evidence_id:
            raise HardwareKnowledgeAdapterError(
                "KNOWLEDGE_EVIDENCE_ID_MISMATCH"
            )
        source = response.get("source")
        if not isinstance(source, dict) or not source.get("source_id"):
            raise HardwareKnowledgeAdapterError(
                "KNOWLEDGE_EVIDENCE_SOURCE_INVALID"
            )
        return response

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, Any] | None = None,
        query: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            status, body = self.transport.request(
                method,
                path,
                json_body=json_body,
                query=query,
            )
        except HardwareKnowledgeAdapterError:
            raise
        except Exception as error:
            raise HardwareKnowledgeAdapterError(
                "KNOWLEDGE_UNAVAILABLE"
            ) from error
        if not isinstance(body, dict):
            raise HardwareKnowledgeAdapterError(
                "KNOWLEDGE_RESPONSE_INVALID",
                status_code=status,
            )
        if 200 <= int(status) < 300:
            return body
        error = body.get("error")
        code = (
            str(error.get("code") or "")
            if isinstance(error, dict)
            else ""
        )
        if not code:
            code = "KNOWLEDGE_UNAVAILABLE" if int(status) >= 500 else "KNOWLEDGE_REQUEST_FAILED"
        raise HardwareKnowledgeAdapterError(code, status_code=int(status))

    def _validate_object(
        self,
        obj: Mapping[str, Any],
        *,
        expected_revision: int | None = None,
    ) -> None:
        self._require_contract_field(
            obj,
            "contract_version",
            CONTRACT_VERSIONS["object"],
        )
        if obj.get("domain") != DOMAIN:
            raise HardwareKnowledgeAdapterError(
                "KNOWLEDGE_DOMAIN_MISMATCH"
            )
        if obj.get("object_type") != OBJECT_TYPE:
            raise HardwareKnowledgeAdapterError(
                "KNOWLEDGE_OBJECT_TYPE_MISMATCH"
            )
        if expected_revision is not None:
            if int(obj.get("revision") or 0) != int(expected_revision):
                raise HardwareKnowledgeAdapterError(
                    "KNOWLEDGE_REVISION_MISMATCH"
                )
        if not isinstance(obj.get("evidence_refs"), list):
            raise HardwareKnowledgeAdapterError(
                "KNOWLEDGE_EVIDENCE_REFS_INVALID"
            )
        if not obj.get("status"):
            raise HardwareKnowledgeAdapterError(
                "KNOWLEDGE_STATUS_MISSING"
            )

    @staticmethod
    def _require_contract_field(
        payload: Mapping[str, Any],
        field: str,
        expected: str,
    ) -> None:
        if payload.get(field) != expected:
            raise HardwareKnowledgeAdapterError(
                "KNOWLEDGE_CONTRACT_VERSION_MISMATCH"
            )

    @staticmethod
    def candidate_id(case_id: str, revision: int) -> str:
        value = str(case_id or "").strip()
        if not value or int(revision) < 1:
            raise HardwareKnowledgeAdapterError(
                "CANDIDATE_CONTRACT_INVALID"
            )
        return f"HC-KNOWLEDGE-{value}-R{int(revision)}"

    @staticmethod
    def public_ref(case_id: str, revision: int) -> str:
        """Return the stable, revisioned public reference for a Hardware Case."""
        return HardwareCaseKnowledgeAdapter.candidate_id(case_id, revision)

    @staticmethod
    def publish_idempotency_key(candidate_id: str, revision: int) -> str:
        return f"{candidate_id}:publish:r{int(revision)}"

    @staticmethod
    def _positive_int(value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        return number if number >= 1 else None

    @staticmethod
    def _optional_text(value: Any) -> str | None:
        text = str(value or "").strip()
        return text or None

    @staticmethod
    def _section_from_path(value: Any) -> str | None:
        if not isinstance(value, (list, tuple)):
            return None
        parts = [str(item).strip() for item in value if str(item).strip()]
        return " / ".join(parts) or None


__all__ = [
    "CONTRACT_VERSIONS",
    "DOMAIN",
    "KNOWLEDGE_CAPABILITY_VERSION",
    "KNOWLEDGE_MAIN_COMMIT",
    "KNOWLEDGE_SHA256",
    "KNOWLEDGE_SOURCE_COMMIT",
    "HardwareCaseKnowledgeAdapter",
    "HardwareKnowledgeAdapterError",
    "HardwareKnowledgeTransport",
    "KnowledgeHttpTransport",
    "KnowledgeReleaseDescriptor",
]
