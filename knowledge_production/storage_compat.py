from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .query import KnowledgeQueryError, KnowledgeQueryService


class StorageKnowledgeCompatibilityError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _terms(value: str) -> list[str]:
    return [
        token
        for token in re.split(r"[^a-zA-Z0-9_./+-]+", value.lower())
        if len(token) >= 2
    ]


@dataclass
class StorageKnowledgeCompatibilityService:
    """Project immutable Knowledge Releases into frozen UKCI-01 V1.0."""

    query_service: KnowledgeQueryService
    knowledge_release_version: str
    service_id: str = "storage_knowledge_service"

    def handle_query(self, payload: dict[str, Any]) -> dict[str, Any]:
        request_id, query = self._validate_request(payload)
        try:
            released = self.query_service.query(
                {
                    "knowledge_release_version": self.knowledge_release_version,
                }
            )
        except KnowledgeQueryError as exc:
            raise StorageKnowledgeCompatibilityError(exc.code) from exc

        fields = query.get("fields") or {}
        device_type = _text(fields.get("device_type"))
        parameter_keys = [
            _text(item) for item in fields.get("parameter_keys") or [] if _text(item)
        ]
        topics = [_text(item) for item in fields.get("topics") or [] if _text(item)]
        query_text = _text(query.get("text"))
        top_k = int((payload.get("options") or {}).get("top_k") or 5)
        top_k = max(1, min(top_k, 50))

        evidence_by_id = {
            _text(item.get("evidence_id")): item
            for item in released.evidences
            if isinstance(item, dict) and _text(item.get("evidence_id"))
        }
        source_by_ref = {
            item.source_ref: item for item in released.source_references
        }

        ranked: list[tuple[int, str, dict[str, Any], list[dict[str, Any]]]] = []
        for obj in released.objects:
            if device_type and _text(obj.device_type).lower() != device_type.lower():
                continue
            score = self._score(
                obj,
                query_text=query_text,
                parameter_keys=parameter_keys,
                topics=topics,
            )
            if score <= 0 and (query_text or parameter_keys or topics):
                continue
            item, evidence = self._project_item(
                obj,
                evidence_by_id=evidence_by_id,
                source_by_ref=source_by_ref,
                requested_parameter_keys=parameter_keys,
            )
            ranked.append((score, obj.object_id, item, evidence))

        ranked.sort(key=lambda row: (-row[0], row[1]))
        selected = ranked[:top_k]
        results = [row[2] for row in selected]
        evidence: list[dict[str, Any]] = []
        for row in selected:
            evidence.extend(row[3])

        warnings = list(released.unknowns_or_gaps)
        return {
            "contract_version": "V1.0",
            "request_id": request_id,
            "service_id": self.service_id,
            "success": True,
            "result": {
                "results": results,
                "total": len(results),
                "provider": (
                    "knowledge-production/"
                    f"{self.knowledge_release_version}"
                ),
            },
            "evidence": evidence,
            "warnings": warnings,
            "error": None,
        }

    def _validate_request(
        self,
        payload: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        if not isinstance(payload, dict):
            raise StorageKnowledgeCompatibilityError(
                "KNOWLEDGE_CONTRACT_INVALID"
            )
        if payload.get("contract_version") not in {"V1.0", "1.0"}:
            raise StorageKnowledgeCompatibilityError(
                "KNOWLEDGE_CONTRACT_INVALID"
            )
        request_id = _text(payload.get("request_id"))
        service_id = _text(payload.get("service_id"))
        query = payload.get("query")
        if not request_id or service_id != self.service_id:
            raise StorageKnowledgeCompatibilityError(
                "KNOWLEDGE_CONTRACT_INVALID"
            )
        if not isinstance(query, dict) or not _text(query.get("text")):
            raise StorageKnowledgeCompatibilityError(
                "KNOWLEDGE_CONTRACT_INVALID"
            )
        if not isinstance(query.get("fields") or {}, dict):
            raise StorageKnowledgeCompatibilityError(
                "KNOWLEDGE_CONTRACT_INVALID"
            )
        return request_id, query

    @staticmethod
    def _score(
        obj,
        *,
        query_text: str,
        parameter_keys: list[str],
        topics: list[str],
    ) -> int:
        title = obj.title.lower()
        content = obj.content.lower()
        tags = {item.lower() for item in obj.tags}
        scope = {item.lower() for item in obj.scope}
        haystack = "\n".join(
            [
                title,
                (obj.summary or "").lower(),
                content,
                " ".join(tags),
                " ".join(scope),
            ]
        )
        score = 0
        for key in parameter_keys:
            lower = key.lower()
            if lower in tags or lower in scope:
                score += 12
            elif lower in haystack:
                score += 8
        for topic in topics:
            lower = topic.lower()
            if lower in tags or lower in scope:
                score += 8
            elif lower in haystack:
                score += 5
        for token in _terms(query_text):
            if token in title:
                score += 4
            elif token in haystack:
                score += 2
        if not query_text and not parameter_keys and not topics:
            score = 1
        return score

    def _project_item(
        self,
        obj,
        *,
        evidence_by_id: dict[str, dict[str, Any]],
        source_by_ref: dict[str, Any],
        requested_parameter_keys: list[str],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        evidences = [
            evidence_by_id[evidence_id]
            for evidence_id in obj.evidence_refs
            if evidence_id in evidence_by_id
        ]
        first_evidence = evidences[0] if evidences else {}
        evidence_source = (
            first_evidence.get("source")
            if isinstance(first_evidence.get("source"), dict)
            else {}
        )
        locator = (
            first_evidence.get("locator")
            if isinstance(first_evidence.get("locator"), dict)
            else {}
        )
        locator_value = (
            locator.get("value")
            if isinstance(locator.get("value"), dict)
            else {}
        )

        source_ref = obj.source_refs[0] if obj.source_refs else ""
        source = source_by_ref.get(source_ref)
        source_id = (
            _text(evidence_source.get("source_id"))
            or (_text(source.source_id) if source is not None else "")
            or obj.object_id
        )
        item = {
            "knowledge_id": obj.object_id,
            "title": obj.title,
            "knowledge_type": obj.object_type.value,
            "summary": obj.summary or obj.content,
            "content": obj.content,
            "content_excerpt": obj.content,
            "source": {
                "source_id": source_id,
                "source_title": (
                    _text(source.title) if source is not None else ""
                ),
                "publisher": (
                    _text(source.publisher) if source is not None else ""
                ),
                "url": (
                    _text(source.official_url) if source is not None else ""
                ),
                "file_name": "",
                "page": locator_value.get("page"),
                "section": _text(locator_value.get("section")),
                "raw_text": _text(first_evidence.get("excerpt")),
            },
            "applicable_device_types": (
                [obj.device_type] if obj.device_type else []
            ),
            "applicable_parameter_keys": self._parameter_keys(
                obj,
                requested_parameter_keys,
            ),
            "verification_status": "human_confirmed",
            "confidence": None,
            "knowledge_release_version": self.knowledge_release_version,
        }

        external_evidence: list[dict[str, Any]] = []
        for evidence_id in obj.evidence_refs:
            raw = evidence_by_id.get(evidence_id)
            if not isinstance(raw, dict):
                continue
            raw_source = (
                raw.get("source")
                if isinstance(raw.get("source"), dict)
                else {}
            )
            external_evidence.append(
                {
                    "source_ref": obj.object_id,
                    "source_type": _text(raw_source.get("source_type"))
                    or "knowledge",
                    "evidence_id": evidence_id,
                    "summary": _text(raw.get("excerpt")),
                    "metadata": {
                        "source_id": _text(raw_source.get("source_id")),
                        "verification_status": "human_confirmed",
                        "knowledge_release_version": (
                            self.knowledge_release_version
                        ),
                        "locator": (
                            raw.get("locator", {}).get("value", {})
                            if isinstance(raw.get("locator"), dict)
                            else {}
                        ),
                    },
                }
            )
        return item, external_evidence

    @staticmethod
    def _parameter_keys(
        obj,
        requested: list[str],
    ) -> list[str]:
        searchable = {
            item.lower(): item
            for item in [*obj.tags, *obj.scope]
            if _text(item)
        }
        matched = [
            key for key in requested if key.lower() in searchable
        ]
        if matched:
            return matched
        return list(dict.fromkeys([*obj.tags, *obj.scope]))
