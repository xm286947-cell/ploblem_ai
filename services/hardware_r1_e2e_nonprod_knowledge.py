"""Managed local NON_PROD Unified Knowledge binding for Hardware R1 E2E.

This module is validation-profile glue only.  It reuses the existing Unified
Knowledge public facade and immutable KnowledgeReleaseService; it does not
introduce another Knowledge schema, store, or publication path.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote

from knowledge_production.public_service import PublicKnowledgeError, PublicKnowledgeService
from knowledge_production.release import KnowledgeReleaseError, KnowledgeReleaseService
from repositories import JsonArtifactRepository
from services.hardware_case_knowledge_adapter import HardwareCaseKnowledgeAdapter


POINTER_PATH = "hardware_r1_e2e/managed_nonprod_release_pointer.json"


class HardwareR1ManagedNonProdError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _json_value(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        result = dump(mode="json")
        if isinstance(result, dict):
            return result
    raise HardwareR1ManagedNonProdError("KNOWLEDGE_RESPONSE_INVALID")


class ManagedNonProdKnowledgeTransport:
    """In-process transport over the existing Unified Knowledge public facade."""

    def __init__(self, repository: JsonArtifactRepository) -> None:
        self.repository = repository
        self.public = PublicKnowledgeService(repository)

    @staticmethod
    def _error_response(error: PublicKnowledgeError) -> tuple[int, dict[str, Any]]:
        not_found = {
            "KNOWLEDGE_OBJECT_NOT_FOUND",
            "KNOWLEDGE_RELEASE_NOT_FOUND",
            "EVIDENCE_NOT_FOUND",
        }
        conflicts = {
            "CANDIDATE_ID_CONFLICT",
            "EVIDENCE_ID_CONFLICT",
            "IDEMPOTENCY_KEY_CONFLICT",
            "OBJECT_VERSION_CONFLICT",
        }
        status = 404 if error.code in not_found else 409 if error.code in conflicts else 422
        return status, {
            "success": False,
            "error": {
                "code": error.code,
                "message": error.code,
                "retryable": False,
            },
        }

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, Any] | None = None,
        query: Mapping[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        verb = str(method or "").upper()
        body = dict(json_body or {})
        params = dict(query or {})
        try:
            if verb == "POST" and path == "/v1/knowledge/evidences":
                return 200, _json_value(self.public.intake_evidence(body))
            if verb == "POST" and path == "/v1/knowledge/candidates":
                return 200, _json_value(self.public.intake_candidate(body))
            if verb == "POST" and path == "/v1/knowledge/reviews":
                return 200, _json_value(self.public.review(body))
            if verb == "POST" and path == "/v1/knowledge/publish":
                return 200, _json_value(self.public.publish(body))
            if verb == "POST" and path == "/v1/knowledge/search":
                return 200, _json_value(self.public.query(body))
            if verb == "GET" and path.startswith("/v1/knowledge/objects/"):
                knowledge_id = unquote(path.rsplit("/", 1)[-1])
                release = str(params.get("knowledge_release_version") or "")
                return 200, _json_value(self.public.get(release, knowledge_id))
            if verb == "GET" and path.startswith("/v1/knowledge/evidences/"):
                evidence_id = unquote(path.rsplit("/", 1)[-1])
                release = str(params.get("knowledge_release_version") or "")
                value = self.public.resolve_evidence(release, evidence_id)
                return 200, dict(value)
            return 404, {
                "success": False,
                "error": {
                    "code": "KNOWLEDGE_ROUTE_NOT_FOUND",
                    "message": "KNOWLEDGE_ROUTE_NOT_FOUND",
                    "retryable": False,
                },
            }
        except PublicKnowledgeError as error:
            return self._error_response(error)


class ManagedNonProdReleaseController:
    """Maintain a rolling pointer over immutable NON_PROD Knowledge Releases."""

    def __init__(
        self,
        repository: JsonArtifactRepository,
        adapter: HardwareCaseKnowledgeAdapter,
        *,
        release_prefix: str,
    ) -> None:
        prefix = str(release_prefix or "").strip()
        if not prefix or not re.fullmatch(r"[A-Za-z0-9._-]+", prefix):
            raise HardwareR1ManagedNonProdError("KNOWLEDGE_RELEASE_VERSION_INVALID")
        self.repository = repository
        self.adapter = adapter
        self.release_prefix = prefix

    def _pointer(self) -> dict[str, Any] | None:
        value = self.repository.load(POINTER_PATH)
        if value is None:
            return None
        if not isinstance(value, dict):
            raise HardwareR1ManagedNonProdError("KNOWLEDGE_RELEASE_POINTER_INVALID")
        release = str(value.get("release_version") or "")
        sequence = value.get("sequence")
        if (
            not release
            or not release.startswith(self.release_prefix + "-R")
            or not isinstance(sequence, int)
            or isinstance(sequence, bool)
            or sequence < 1
        ):
            raise HardwareR1ManagedNonProdError("KNOWLEDGE_RELEASE_POINTER_INVALID")
        return dict(value)

    def bind_existing(self) -> dict[str, Any]:
        pointer = self._pointer()
        if pointer is not None:
            self.adapter.knowledge_release_version = str(pointer["release_version"])
        return self.status()

    def status(self) -> dict[str, Any]:
        pointer = self._pointer()
        return {
            "mode": "LOCAL_NON_PROD",
            "managed_release": True,
            "release_prefix": self.release_prefix,
            "release_version": (
                str(pointer["release_version"]) if pointer is not None else None
            ),
            "sequence": int(pointer["sequence"]) if pointer is not None else 0,
        }

    def ensure_queryable_release(self) -> dict[str, Any]:
        """Build/reuse a snapshot containing the latest published objects.

        A release version is immutable.  If the current pointer now conflicts
        because a new object was published, advance to a new version instead of
        mutating the old release.
        """
        pointer = self._pointer()
        sequence = int(pointer["sequence"]) if pointer is not None else 1
        current = str(pointer["release_version"]) if pointer is not None else None
        service = KnowledgeReleaseService(self.repository)

        if current:
            try:
                manifest = service.build(current, created_at=datetime.now(timezone.utc))
            except KnowledgeReleaseError as error:
                if error.code != "KNOWLEDGE_RELEASE_VERSION_CONFLICT":
                    raise HardwareR1ManagedNonProdError(error.code) from error
                sequence += 1
            else:
                self.adapter.knowledge_release_version = current
                return {
                    **self.status(),
                    "release_version": current,
                    "snapshot_hash": manifest.snapshot_hash,
                    "idempotent_reuse": True,
                }

        # Pointer loss or a new publish may leave an already-used version name.
        # Advance deterministically until an unused version accepts this snapshot.
        for _ in range(1000):
            version = f"{self.release_prefix}-R{sequence:06d}"
            try:
                manifest = service.build(version, created_at=datetime.now(timezone.utc))
            except KnowledgeReleaseError as error:
                if error.code == "KNOWLEDGE_RELEASE_VERSION_CONFLICT":
                    sequence += 1
                    continue
                raise HardwareR1ManagedNonProdError(error.code) from error
            self.repository.save(
                POINTER_PATH,
                {
                    "mode": "LOCAL_NON_PROD",
                    "sequence": sequence,
                    "release_version": version,
                    "snapshot_hash": manifest.snapshot_hash,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            self.adapter.knowledge_release_version = version
            return {
                **self.status(),
                "release_version": version,
                "snapshot_hash": manifest.snapshot_hash,
                "idempotent_reuse": False,
            }
        raise HardwareR1ManagedNonProdError("KNOWLEDGE_RELEASE_SEQUENCE_EXHAUSTED")


def create_managed_nonprod_environment(
    root: str | Path,
    *,
    release_prefix: str,
) -> tuple[
    HardwareCaseKnowledgeAdapter,
    ManagedNonProdReleaseController,
    dict[str, Any],
]:
    repository = JsonArtifactRepository(Path(root))
    transport = ManagedNonProdKnowledgeTransport(repository)
    pointer = repository.load(POINTER_PATH)
    initial_release = str(
        pointer.get("release_version")
        if isinstance(pointer, dict) and pointer.get("release_version")
        else release_prefix
    )
    adapter = HardwareCaseKnowledgeAdapter(
        transport,
        knowledge_release_version=initial_release,
    )
    controller = ManagedNonProdReleaseController(
        repository,
        adapter,
        release_prefix=release_prefix,
    )
    status = controller.bind_existing()
    return adapter, controller, status


__all__ = [
    "HardwareR1ManagedNonProdError",
    "ManagedNonProdKnowledgeTransport",
    "ManagedNonProdReleaseController",
    "create_managed_nonprod_environment",
]
