"""Public Major Issue -> Unified Knowledge publication boundary.

The Major domain owns confirmation, revision identity, and source evidence.
The Knowledge domain owns candidate status, review, object version, and
release binding.  This module deliberately contains no Knowledge repository
or Knowledge domain-model imports; the only hand-off is the public contract
and the injected intake port.
"""
from __future__ import annotations

from hashlib import sha256
import json
from typing import Any, Mapping, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field

from quality_knowledge.major_cases.repository import MajorKnowledgeRepository
from quality_knowledge.problem_refs import SourceProblemItrRefV1

from .major_case_publish import (
    MajorCasePublishAdapter,
    PublishValidationError,
)


PUBLICATION_CONTRACT = "major-knowledge-publication/v1"
KNOWLEDGE_CANDIDATE_CONTRACT = "knowledge-candidate/v1"
KNOWLEDGE_RELEASE_VERSION = "KP-STORAGE-RC1-VALIDATION-001"
PUBLICATION_STATUS = "SUBMITTED"


class PublicationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class PublicationSource(PublicationModel):
    source_type: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    business_ref: str = Field(min_length=1)


class PublicationEvidence(PublicationModel):
    contract_version: str = "common-evidence/v1.0"
    evidence_id: str = Field(min_length=1)
    evidence_type: str = "SOURCE_EXCERPT"
    source: dict[str, Any]
    locator: dict[str, Any]
    excerpt: str = Field(min_length=1)
    source_text: str | None = None
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_ref: str = Field(min_length=1)
    source_reference: str | None = None
    producer_domain: str = "MajorIssue"
    producer_object_id: str = Field(min_length=1)
    producer_object_version: int = Field(default=1, ge=1)
    verification_status: str = "HUMAN_CONFIRMED"
    evidence_status: str = "BOUND"
    created_at: str | None = None


class KnowledgeCandidatePayload(PublicationModel):
    candidate_id: str = Field(min_length=1)
    object_type: str = Field(min_length=1)
    title: str = Field(min_length=1)
    summary: str | None = None
    content: str = Field(min_length=1)
    scope: list[str] = Field(min_length=1)
    conditions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    business_source_id: str = Field(min_length=1)
    business_source_version: str = Field(min_length=1)
    source_refs: list[str] = Field(min_length=1)
    evidence_refs: list[str] = Field(min_length=1)
    producer: str = "MAJOR_ISSUE"
    contract_version: str = PUBLICATION_CONTRACT


class MajorKnowledgePublicationContract(PublicationModel):
    contract_version: str = Field(
        default=PUBLICATION_CONTRACT,
        pattern=r"^major-knowledge-publication/v1$",
    )
    producer: str = "MAJOR_ISSUE"
    source: PublicationSource
    major_case_ref: str = Field(min_length=1)
    major_confirmed_revision: str = Field(min_length=1)
    publication_revision: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    knowledge_candidates: list[KnowledgeCandidatePayload] = Field(min_length=1)
    evidence_refs: list[str] = Field(min_length=1)
    evidence_bindings: list[PublicationEvidence] = Field(min_length=1)
    source_refs: list[str] = Field(min_length=1)
    publication_status: str = Field(
        default=PUBLICATION_STATUS,
        pattern=r"^SUBMITTED$",
    )


class MajorKnowledgePublicationSubmission(PublicationModel):
    status: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    publication_revision: str = Field(min_length=1)
    candidate_ids: list[str] = Field(default_factory=list)
    knowledge_release_version: str | None = None


class KnowledgeReleaseBinding(PublicationModel):
    contract_version: str = "knowledge-release-binding/v1.0"
    major_publication_revision: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    knowledge_object_ref: str = Field(min_length=1)
    knowledge_object_version: int = Field(ge=1)
    knowledge_release_version: str = Field(min_length=1)


class KnowledgeCandidateIntakePort(Protocol):
    """Public port implemented by Unified Knowledge, not by Major storage."""

    def submit(
        self,
        publication: MajorKnowledgePublicationContract,
    ) -> MajorKnowledgePublicationSubmission:
        ...


class MajorKnowledgePublicationError(RuntimeError):
    """Stable failure codes for the Major publication boundary."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


_OBJECT_TYPE = {
    "ISSUE_FACT": "FACT",
    "ROOT_CAUSE": "DIAGNOSTIC",
    "ACTION": "SOLUTION",
    "VERIFICATION": "REQUIREMENT",
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any, *, length: int = 24) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()[:length]


class MajorKnowledgePublicationAdapter:
    """Project one confirmed Major Event into the frozen public contract."""

    def __init__(self, repository: MajorKnowledgeRepository):
        self.repository = repository
        self.major_publisher = MajorCasePublishAdapter(repository)

    def build_publication(
        self,
        event_id: str,
        *,
        existing_mapping: Mapping[str, Any] | None = None,
    ) -> MajorKnowledgePublicationContract:
        try:
            major = self.major_publisher.build_candidate(
                event_id,
                existing_mapping=existing_mapping,
            )
        except PublishValidationError as exc:
            if exc.code in {
                "MAJOR_CASE_NOT_ACTIVE",
                "NO_PUBLISHABLE_CONFIRMED_FACT",
                "KNOWLEDGE_REVISION_UNAVAILABLE",
            }:
                raise MajorKnowledgePublicationError("MAJOR_NOT_CONFIRMED") from exc
            raise MajorKnowledgePublicationError(exc.code) from exc

        try:
            source_ref = SourceProblemItrRefV1.from_input(major["standard_itr"])
        except ValueError as exc:
            raise MajorKnowledgePublicationError("SOURCE_REF_INVALID") from exc

        entries = list(major.get("knowledge_entries") or [])
        sections = list((major.get("raw_evidence") or {}).get("sections") or [])
        if not entries or not sections:
            raise MajorKnowledgePublicationError("EVIDENCE_MISSING")

        sections_by_entry: dict[str, list[dict[str, Any]]] = {}
        for section in sections:
            entry_id = str(section.get("entry_id") or "")
            if not entry_id or not section.get("revision_id"):
                raise MajorKnowledgePublicationError("EVIDENCE_MISSING")
            if not section.get("source_type") or not section.get("source_id"):
                raise MajorKnowledgePublicationError("EVIDENCE_MISSING")
            if not str(section.get("raw_text") or "").strip():
                raise MajorKnowledgePublicationError("EVIDENCE_MISSING")
            sections_by_entry.setdefault(entry_id, []).append(section)

        for entry in entries:
            if not sections_by_entry.get(str(entry.get("entry_id") or "")):
                raise MajorKnowledgePublicationError("EVIDENCE_MISSING")

        major_case_ref = f"MAJOR_CASE:{source_ref.public_ref}"
        business_source_id = f"MAJOR_PUBLICATION:{source_ref.public_ref}"
        evidence_bindings = self._evidence_bindings(
            major=major,
            major_case_ref=major_case_ref,
            sections=sections,
        )
        evidence_by_entry: dict[str, list[str]] = {}
        for evidence in evidence_bindings:
            anchor = str(evidence.locator.get("anchor") or "")
            entry_id = anchor.removeprefix("entry:").split(":revision:", 1)[0]
            if not entry_id:
                raise MajorKnowledgePublicationError("EVIDENCE_MISSING")
            evidence_by_entry.setdefault(entry_id, []).append(evidence.evidence_id)

        revision_material = {
            "business_ref": source_ref.public_ref,
            "major_confirmed_revision": major["knowledge_revision"],
            "entries": [
                {
                    "entry_id": entry.get("entry_id"),
                    "revision_id": entry.get("current_revision_id"),
                    "entry_type": entry.get("entry_type"),
                    "content": entry.get("content"),
                }
                for entry in sorted(entries, key=lambda item: str(item.get("entry_id") or ""))
            ],
            "evidence": [item.model_dump(mode="json") for item in evidence_bindings],
        }
        publication_revision = f"MJP-{_digest(revision_material)}"
        idempotency_key = (
            f"MAJOR_ISSUE|{source_ref.public_ref}|{publication_revision}"
        )
        versioned_source_ref = (
            f"MAJOR_ISSUE:{business_source_id}@{publication_revision}"
        )
        scope = [
            value
            for value in (
                major["enriched_case"]["business_context"].get("domain"),
                major["enriched_case"]["business_context"].get("group_code"),
            )
            if value
        ] or ["MAJOR_ISSUE"]

        candidates: list[KnowledgeCandidatePayload] = []
        for entry in sorted(entries, key=lambda item: str(item.get("entry_id") or "")):
            entry_id = str(entry.get("entry_id") or "")
            entry_type = str(entry.get("entry_type") or "")
            content = str(entry.get("content") or "").strip()
            if entry_type not in _OBJECT_TYPE or not content:
                raise MajorKnowledgePublicationError("PUBLICATION_CONTRACT_INVALID")
            candidate_id = (
                f"MJC-{_digest([publication_revision, entry_id, content])}"
            )
            candidates.append(
                KnowledgeCandidatePayload(
                    candidate_id=candidate_id,
                    object_type=_OBJECT_TYPE[entry_type],
                    title=f"{major.get('title') or source_ref.public_ref} · {entry_type}",
                    summary=content,
                    content=content,
                    scope=scope,
                    tags=["MAJOR_ISSUE", entry_type],
                    business_source_id=business_source_id,
                    business_source_version=publication_revision,
                    source_refs=[source_ref.public_ref, versioned_source_ref],
                    evidence_refs=evidence_by_entry[entry_id],
                    producer="MAJOR_ISSUE",
                    contract_version=PUBLICATION_CONTRACT,
                )
            )

        return MajorKnowledgePublicationContract(
            source=PublicationSource(
                source_type="MAJOR_EVENT",
                source_id=event_id,
                business_ref=source_ref.public_ref,
            ),
            major_case_ref=major_case_ref,
            major_confirmed_revision=major["knowledge_revision"],
            publication_revision=publication_revision,
            idempotency_key=idempotency_key,
            knowledge_candidates=candidates,
            evidence_refs=[item.evidence_id for item in evidence_bindings],
            evidence_bindings=evidence_bindings,
            source_refs=[source_ref.public_ref, versioned_source_ref],
            publication_status=PUBLICATION_STATUS,
        )

    def submit(
        self,
        event_id: str,
        intake_port: KnowledgeCandidateIntakePort,
        *,
        existing_mapping: Mapping[str, Any] | None = None,
    ) -> MajorKnowledgePublicationSubmission:
        publication = self.build_publication(
            event_id,
            existing_mapping=existing_mapping,
        )
        try:
            return intake_port.submit(publication)
        except MajorKnowledgePublicationError:
            raise
        except Exception as exc:
            code = getattr(exc, "code", "KNOWLEDGE_INTAKE_UNAVAILABLE")
            raise MajorKnowledgePublicationError(str(code)) from exc

    @staticmethod
    def bind_knowledge_release(
        publication: MajorKnowledgePublicationContract,
        *,
        candidate_id: str,
        knowledge_object_ref: str,
        knowledge_object_version: int,
        knowledge_release_version: str,
    ) -> KnowledgeReleaseBinding:
        if candidate_id not in {
            candidate.candidate_id for candidate in publication.knowledge_candidates
        }:
            raise MajorKnowledgePublicationError("KNOWLEDGE_CONTRACT_INVALID")
        if not knowledge_object_ref or knowledge_object_version < 1:
            raise MajorKnowledgePublicationError("KNOWLEDGE_CONTRACT_INVALID")
        return KnowledgeReleaseBinding(
            major_publication_revision=publication.publication_revision,
            candidate_id=candidate_id,
            knowledge_object_ref=knowledge_object_ref,
            knowledge_object_version=knowledge_object_version,
            knowledge_release_version=knowledge_release_version,
        )

    @staticmethod
    def _evidence_bindings(
        *,
        major: Mapping[str, Any],
        major_case_ref: str,
        sections: Sequence[Mapping[str, Any]],
    ) -> list[PublicationEvidence]:
        result: list[PublicationEvidence] = []
        for section in sections:
            raw_text = str(section.get("raw_text") or "").strip()
            content_hash = sha256(raw_text.encode("utf-8")).hexdigest()
            identity = {
                "source_type": section.get("source_type"),
                "source_id": section.get("source_id"),
                "revision_id": section.get("revision_id"),
                "entry_id": section.get("entry_id"),
                "raw_text": raw_text,
            }
            evidence_id = f"MJR-EVD-{_digest(identity)}"
            source_type = str(section.get("source_type") or "MAJOR_EVENT")
            source_id = str(section.get("source_id") or major["event_id"])
            source_version = str(section.get("revision_id") or major["knowledge_revision"])
            source_ref = f"{source_type}:{source_id}@{source_version}"
            result.append(
                PublicationEvidence(
                    evidence_id=evidence_id,
                    source={
                        "source_type": source_type,
                        "source_id": source_id,
                        "source_version": source_version,
                    },
                    locator={
                        "page": section.get("page"),
                        "section": section.get("section"),
                        "anchor": (
                            f"entry:{section.get('entry_id')}:"
                            f"revision:{section.get('revision_id')}"
                        ),
                    },
                    excerpt=raw_text,
                    source_text=raw_text,
                    content_hash=content_hash,
                    source_ref=source_ref,
                    source_reference=section.get("url") or None,
                    producer_object_id=major_case_ref,
                )
            )
        return result


__all__ = [
    "KNOWLEDGE_CANDIDATE_CONTRACT",
    "KNOWLEDGE_RELEASE_VERSION",
    "KnowledgeCandidateIntakePort",
    "KnowledgeCandidatePayload",
    "KnowledgeReleaseBinding",
    "MajorKnowledgePublicationAdapter",
    "MajorKnowledgePublicationContract",
    "MajorKnowledgePublicationError",
    "MajorKnowledgePublicationSubmission",
    "PUBLICATION_CONTRACT",
    "PUBLICATION_STATUS",
    "PublicationEvidence",
    "PublicationSource",
]
