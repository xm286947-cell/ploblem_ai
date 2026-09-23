from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from knowledge_production import (
    BusinessCandidateIntakeError,
    BusinessCandidateIntakeService,
    EvidenceLocation,
    KnowledgeCandidate,
    KnowledgeCandidateService,
    business_source_ref_key,
    source_ref_key,
)
from repositories import JsonArtifactRepository
from runtime.contracts import EvidenceLocator, EvidenceReference, SourceRef


CREATED_AT = datetime(2026, 9, 23, 6, 0, tzinfo=timezone.utc)


def _seed_business_evidence(repository: JsonArtifactRepository) -> str:
    evidence = EvidenceReference(
        evidence_id="EVD-HCASE-001",
        source=SourceRef(
            source_id="CASE-FLASH-WRITE-001",
            source_type="HISTORICAL_CASE",
            revision="V3",
            content_hash=None,
            fingerprint="historical-case:CASE-FLASH-WRITE-001:V3",
            uri=None,
            metadata={"contract_version": "historical-case/v1"},
        ),
        locator=EvidenceLocator(
            type="BUSINESS_RECORD",
            value={
                "case_id": "CASE-FLASH-WRITE-001",
                "field": "root_cause",
            },
        ),
        excerpt="高频小写导致介质写放大和寿命异常消耗",
        metadata={"evidence_status": "BOUND"},
    )
    repository.save(
        "knowledge/production/evidence/EVD-HCASE-001.json",
        evidence.model_dump(mode="json"),
    )
    return evidence.evidence_id


def _business_payload(evidence_refs: list[str] | None = None) -> dict:
    return {
        "candidate_id": "BC-HCASE-001",
        "candidate_source_type": "BUSINESS",
        "business_source_type": "HISTORICAL_CASE",
        "business_source_id": "CASE-FLASH-WRITE-001",
        "business_source_version": "V3",
        "object_type": "SOLUTION",
        "title": "Merge small writes",
        "content": "Merge or cache small writes to reduce media write amplification.",
        "device_type": "GENERIC",
        "scope": ["storage_lifetime"],
        "conditions": ["high-frequency small writes"],
        "limitations": ["requires durability design"],
        "tags": ["write_amplification", "merge_write"],
        "source_refs": [],
        "evidence_refs": evidence_refs or [],
        "confidence": 0.9,
        "created_at": CREATED_AT.isoformat(),
        "producer": "historical-case/v1",
        "contract_version": "knowledge-candidate/v1",
        "metadata": {"case_title": "高频小写导致存储寿命异常消耗"},
    }


def test_kp_d01_historical_case_intake_preserves_business_provenance(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    evidence_id = _seed_business_evidence(repository)
    service = BusinessCandidateIntakeService(repository)

    candidate = service.intake(_business_payload([evidence_id]))

    expected_ref = business_source_ref_key(
        "HISTORICAL_CASE",
        "CASE-FLASH-WRITE-001",
        "V3",
    )
    assert candidate.candidate_source_type == "BUSINESS"
    assert candidate.business_source_type == "HISTORICAL_CASE"
    assert candidate.business_source_id == "CASE-FLASH-WRITE-001"
    assert candidate.business_source_version == "V3"
    assert expected_ref in candidate.source_refs
    assert candidate.evidence_refs == [evidence_id]
    assert candidate.producer == "historical-case/v1"
    assert candidate.contract_version == "knowledge-candidate/v1"
    assert candidate.status == "CANDIDATE"
    assert candidate.metadata["business_provenance"] == {
        "source_type": "HISTORICAL_CASE",
        "source_id": "CASE-FLASH-WRITE-001",
        "source_version": "V3",
    }


def test_kp_d01_business_candidate_can_enter_without_evidence_but_not_publish(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    candidate = BusinessCandidateIntakeService(repository).intake(
        _business_payload([])
    )

    assert candidate.evidence_refs == []
    assert candidate.status == "CANDIDATE"
    assert repository.list("knowledge/production/published") == []


def test_kp_d01_business_and_external_candidates_share_one_store(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    evidence_id = _seed_business_evidence(repository)
    BusinessCandidateIntakeService(repository).intake(
        _business_payload([evidence_id])
    )

    page_text = "Wear leveling distributes erase cycles."
    document_hash = hashlib.sha256(b"official-pdf").hexdigest()
    repository.save(
        "knowledge/source_documents/SRC/R1/source_document.json",
        {
            "source_id": "SRC",
            "source_version": "R1",
            "publisher": "Official",
            "title": "Storage Spec",
            "document_type": "PDF",
            "official_url": "https://example.invalid/spec.pdf",
            "content_hash": document_hash,
        },
    )
    repository.save(
        "knowledge/source_documents/SRC/R1/structured_document.json",
        {
            "blocks": [
                {
                    "page": 1,
                    "section": "WEAR LEVELING",
                    "source_anchor": "page:1",
                    "source_text": page_text,
                    "content_hash": hashlib.sha256(
                        page_text.encode("utf-8")
                    ).hexdigest(),
                }
            ]
        },
    )
    candidates = KnowledgeCandidateService(repository)
    external_evidence = candidates.bind_evidence(
        EvidenceLocation(
            source_id="SRC",
            source_version="R1",
            page=1,
            section="WEAR LEVELING",
            source_anchor="page:1",
        )
    )
    candidates.save_candidate(
        KnowledgeCandidate(
            candidate_id="EXT-001",
            candidate_source_type="EXTERNAL_SOURCE",
            object_type="CONCEPT",
            title="Wear Leveling",
            content="Wear leveling distributes erase cycles.",
            evidence_refs=[external_evidence.evidence_id],
            source_refs=[source_ref_key("SRC", "R1")],
            extraction_version="kp-d01-regression",
            confidence=0.95,
            producer="KNOWLEDGE_EXTRACTION",
            contract_version="knowledge-candidate/v1",
            created_at=CREATED_AT,
        )
    )

    stored = repository.list("knowledge/production/candidates")
    assert {path.name for path in stored} == {
        "BC-HCASE-001.json",
        "EXT-001.json",
    }
    assert not repository.resolve(
        "knowledge/production/business_candidates"
    ).exists()


def test_kp_d01_same_business_candidate_is_idempotent(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    service = BusinessCandidateIntakeService(repository)
    payload = _business_payload([])

    first = service.intake(payload)
    second = service.intake(payload)

    assert first == second
    assert len(repository.list("knowledge/production/candidates")) == 1


def test_kp_d01_same_candidate_id_different_content_is_blocked(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    service = BusinessCandidateIntakeService(repository)
    service.intake(_business_payload([]))
    changed = _business_payload([])
    changed["content"] = "Different semantic content."

    with pytest.raises(
        BusinessCandidateIntakeError,
        match="CANDIDATE_ID_CONFLICT",
    ):
        service.intake(changed)


def test_kp_d01_missing_referenced_business_evidence_is_blocked(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)

    with pytest.raises(
        BusinessCandidateIntakeError,
        match="EVIDENCE_MISSING",
    ):
        BusinessCandidateIntakeService(repository).intake(
            _business_payload(["EVD-MISSING"])
        )


def test_kp_d01_contract_version_is_fail_closed(tmp_path: Path) -> None:
    payload = _business_payload([])
    payload["contract_version"] = "knowledge-candidate/v2"

    with pytest.raises(
        BusinessCandidateIntakeError,
        match="CANDIDATE_CONTRACT_INVALID",
    ):
        BusinessCandidateIntakeService(
            JsonArtifactRepository(tmp_path)
        ).intake(payload)


def test_kp_d01_required_business_metadata_is_not_inferred(
    tmp_path: Path,
) -> None:
    payload = _business_payload([])
    del payload["created_at"]

    with pytest.raises(
        BusinessCandidateIntakeError,
        match="CANDIDATE_CONTRACT_INVALID",
    ):
        BusinessCandidateIntakeService(
            JsonArtifactRepository(tmp_path)
        ).intake(payload)


def test_kp_d01_supported_business_source_types() -> None:
    for source_type in [
        "STORAGE",
        "MAJOR_ISSUE",
        "HISTORICAL_CASE",
        "HARDWARE_CASE",
        "OTHER",
    ]:
        ref = business_source_ref_key(source_type, "ID-1", "V1")
        assert ref == f"{source_type}:ID-1@V1"
