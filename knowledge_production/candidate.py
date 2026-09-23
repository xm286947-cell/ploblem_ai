from __future__ import annotations

import hashlib

from repositories import JsonArtifactRepository, RepositoryError
from runtime.contracts import EvidenceLocator, EvidenceReference, SourceRef

from .models import EvidenceLocation, KnowledgeCandidate


class KnowledgeCandidateError(RuntimeError):
    """Stable knowledge-production domain error."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def source_ref_key(source_id: str, source_version: str) -> str:
    return f"{source_id}@{source_version}"


def _stable_evidence_id(location: EvidenceLocation, content_hash: str) -> str:
    identity = "|".join(
        [
            location.source_id,
            location.source_version,
            str(location.page),
            location.section or "",
            location.source_anchor or "",
            content_hash,
        ]
    )
    return "EVD-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


class KnowledgeCandidateService:
    """Bind trusted source evidence and persist evidence-backed candidates.

    This service reuses the Runtime EvidenceReference contract.  The caller may
    provide only source location.  Evidence text is always rebound from the
    parsed SourceDocument and is never accepted from AI output.
    """

    def __init__(self, repository: JsonArtifactRepository) -> None:
        self.repository = repository

    def bind_evidence(self, location: EvidenceLocation) -> EvidenceReference:
        base = (
            f"knowledge/source_documents/"
            f"{location.source_id}/{location.source_version}"
        )
        try:
            manifest = self.repository.load(
                f"{base}/source_document.json", required=True
            )
            structured = self.repository.load(
                f"{base}/structured_document.json", required=True
            )
        except RepositoryError as exc:
            raise KnowledgeCandidateError("SOURCE_UNAVAILABLE") from exc

        assert manifest is not None and structured is not None
        blocks = structured.get("blocks")
        if not isinstance(blocks, list):
            raise KnowledgeCandidateError("EVIDENCE_MISSING")

        matches: list[dict] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if block.get("page") != location.page:
                continue
            if (
                location.section is not None
                and block.get("section") != location.section
            ):
                continue
            if (
                location.source_anchor is not None
                and block.get("source_anchor") != location.source_anchor
            ):
                continue
            matches.append(block)

        if len(matches) != 1:
            raise KnowledgeCandidateError("EVIDENCE_MISSING")

        block = matches[0]
        source_text = block.get("source_text")
        block_hash = block.get("content_hash")
        if (
            not isinstance(source_text, str)
            or not source_text
            or not isinstance(block_hash, str)
            or not block_hash
        ):
            raise KnowledgeCandidateError("EVIDENCE_MISSING")

        document_hash = str(manifest.get("content_hash") or "")
        if not document_hash:
            raise KnowledgeCandidateError("SOURCE_UNAVAILABLE")

        evidence_id = _stable_evidence_id(location, block_hash)
        evidence = EvidenceReference(
            evidence_id=evidence_id,
            source=SourceRef(
                source_id=location.source_id,
                source_type=str(manifest.get("document_type") or "PDF"),
                revision=location.source_version,
                content_hash=document_hash,
                fingerprint=f"sha256:{document_hash}",
                uri=manifest.get("official_url"),
                metadata={
                    "publisher": manifest.get("publisher"),
                    "title": manifest.get("title"),
                },
            ),
            locator=EvidenceLocator(
                type="PAGE",
                value={
                    "page": location.page,
                    "section": block.get("section"),
                    "source_anchor": block.get("source_anchor"),
                    "content_hash": block_hash,
                },
            ),
            excerpt=source_text,
            metadata={
                "evidence_status": "BOUND",
                "source_version": location.source_version,
            },
        )

        evidence_path = (
            f"knowledge/production/evidence/{evidence.evidence_id}.json"
        )
        payload = evidence.model_dump(mode="json")
        existing = self.repository.load(evidence_path)
        if existing is not None and existing != payload:
            raise KnowledgeCandidateError("EVIDENCE_ID_CONFLICT")
        self.repository.save(evidence_path, payload)
        return evidence

    def save_candidate(
        self, candidate: KnowledgeCandidate
    ) -> KnowledgeCandidate:
        if candidate.status != "CANDIDATE":
            raise KnowledgeCandidateError("CANDIDATE_STATUS_INVALID")

        evidence_payloads: list[dict] = []
        for evidence_id in candidate.evidence_refs:
            payload = self.repository.load(
                f"knowledge/production/evidence/{evidence_id}.json"
            )
            if payload is None:
                raise KnowledgeCandidateError("EVIDENCE_MISSING")
            evidence_payloads.append(payload)

        expected_source_refs: set[str] = set()
        for evidence in evidence_payloads:
            source = evidence.get("source")
            if not isinstance(source, dict):
                raise KnowledgeCandidateError("EVIDENCE_MISSING")
            source_id = str(source.get("source_id") or "")
            source_version = str(source.get("revision") or "")
            if not source_id or not source_version:
                raise KnowledgeCandidateError("SOURCE_TRACEABILITY_INVALID")
            expected_source_refs.add(source_ref_key(source_id, source_version))

        if not expected_source_refs.issubset(set(candidate.source_refs)):
            raise KnowledgeCandidateError("SOURCE_TRACEABILITY_INVALID")

        for source_ref in expected_source_refs:
            source_id, source_version = source_ref.rsplit("@", 1)
            if self.repository.load(
                (
                    f"knowledge/source_documents/{source_id}/"
                    f"{source_version}/source_document.json"
                )
            ) is None:
                raise KnowledgeCandidateError("SOURCE_UNAVAILABLE")

        candidate_path = (
            f"knowledge/production/candidates/{candidate.candidate_id}.json"
        )
        payload = candidate.model_dump(mode="json")
        existing = self.repository.load(candidate_path)
        if existing is not None and existing != payload:
            raise KnowledgeCandidateError("CANDIDATE_ID_CONFLICT")
        self.repository.save(candidate_path, payload)
        return candidate
