from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from knowledge_production import (
    BusinessCandidateIntakeService,
    EvidenceLocation,
    KnowledgeCandidate,
    KnowledgeCandidateService,
    KnowledgeEvaluationError,
    KnowledgeEvaluationService,
    business_source_ref_key,
    source_ref_key,
)
from repositories import JsonArtifactRepository
from runtime.contracts import EvidenceLocator, EvidenceReference, SourceRef


CREATED_AT = datetime(2026, 9, 23, 7, 0, tzinfo=timezone.utc)


def _seed_business_evidence(
    repository: JsonArtifactRepository,
    evidence_id: str = "EVD-BIZ-001",
) -> str:
    evidence = EvidenceReference(
        evidence_id=evidence_id,
        source=SourceRef(
            source_id="ITR-001",
            source_type="REPORT",
            revision="V1",
            content_hash=None,
            fingerprint="report:ITR-001:V1",
            uri=None,
            metadata={},
        ),
        locator=EvidenceLocator(
            type="BUSINESS_RECORD",
            value={"itr_id": "ITR-001", "field": "root_cause"},
        ),
        excerpt="高频小写导致额外介质写入。",
        metadata={"evidence_status": "BOUND"},
    )
    repository.save(
        f"knowledge/production/evidence/{evidence_id}.json",
        evidence.model_dump(mode="json"),
    )
    return evidence_id


def _business_candidate(
    repository: JsonArtifactRepository,
    *,
    candidate_id: str = "BC-001",
    evidence_refs: list[str] | None = None,
    title: str = "Merge small writes",
    content: str = "Merge or cache small writes to reduce write amplification.",
) -> KnowledgeCandidate:
    return BusinessCandidateIntakeService(repository).intake(
        {
            "candidate_id": candidate_id,
            "candidate_source_type": "BUSINESS",
            "business_source_type": "HISTORICAL_CASE",
            "business_source_id": "CASE-001",
            "business_source_version": "V3",
            "object_type": "SOLUTION",
            "title": title,
            "content": content,
            "device_type": "GENERIC",
            "scope": ["storage_lifetime"],
            "conditions": ["high-frequency small writes"],
            "limitations": ["requires durability design"],
            "tags": ["write_amplification"],
            "source_refs": [],
            "evidence_refs": evidence_refs or [],
            "confidence": 0.9,
            "created_at": CREATED_AT.isoformat(),
            "producer": "historical-case/v1",
            "contract_version": "knowledge-candidate/v1",
            "metadata": {},
        }
    )


def _published(
    repository: JsonArtifactRepository,
    *,
    object_id: str = "KO-001",
    title: str = "Merge small writes",
    content: str = "Merge or cache small writes to reduce write amplification.",
    status: str = "ACTIVE",
) -> None:
    repository.save(
        f"knowledge/production/published/{object_id}.json",
        {
            "object_id": object_id,
            "object_type": "SOLUTION",
            "title": title,
            "content": content,
            "device_type": "GENERIC",
            "scope": ["storage_lifetime"],
            "status": status,
            "object_version": "1",
        },
    )


def _seed_external_candidate(
    repository: JsonArtifactRepository,
) -> KnowledgeCandidate:
    text = "Percentage Used reports an estimate of NVM subsystem life consumed."
    digest = hashlib.sha256(b"official-pdf").hexdigest()
    repository.save(
        "knowledge/source_documents/NVME/2.0d/source_document.json",
        {
            "source_id": "NVME",
            "source_version": "2.0d",
            "publisher": "NVM Express",
            "title": "NVM Express Base Specification",
            "document_type": "PDF",
            "content_hash": digest,
            "retrieval_status": "PARSED",
        },
    )
    repository.save(
        "knowledge/source_documents/NVME/2.0d/structured_document.json",
        {
            "blocks": [
                {
                    "page": 200,
                    "section": "SMART / Health",
                    "source_anchor": "page:200",
                    "source_text": text,
                    "content_hash": hashlib.sha256(
                        text.encode("utf-8")
                    ).hexdigest(),
                }
            ]
        },
    )
    service = KnowledgeCandidateService(repository)
    evidence = service.bind_evidence(
        EvidenceLocation(
            source_id="NVME",
            source_version="2.0d",
            page=200,
            section="SMART / Health",
            source_anchor="page:200",
        )
    )
    candidate = KnowledgeCandidate(
        candidate_id="EXT-001",
        candidate_source_type="EXTERNAL_SOURCE",
        object_type="DIAGNOSTIC",
        title="Percentage Used",
        content="Percentage Used estimates NVM subsystem life consumed.",
        device_type="SSD",
        scope=["health"],
        evidence_refs=[evidence.evidence_id],
        source_refs=[source_ref_key("NVME", "2.0d")],
        extraction_version="kp-d02-test",
        confidence=0.95,
        producer="KNOWLEDGE_EXTRACTION",
        contract_version="knowledge-candidate/v1",
        created_at=CREATED_AT,
    )
    return service.save_candidate(candidate)


def test_kp_d02_valid_business_candidate_is_review_ready(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    evidence_id = _seed_business_evidence(repository)
    candidate = _business_candidate(repository, evidence_refs=[evidence_id])

    result = KnowledgeEvaluationService(repository).evaluate(candidate)

    assert result.evidence_status == "VALID"
    assert result.source_valid is True
    assert result.duplicate_status == "NEW"
    assert result.conflict_status == "NONE"
    assert result.review_ready is True
    assert result.publish_readiness is True


def test_kp_d02_missing_evidence_is_explicit_and_fail_closed(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    candidate = _business_candidate(repository, evidence_refs=[])

    result = KnowledgeEvaluationService(repository).evaluate(candidate)

    assert result.evidence_status == "INVALID"
    assert "EVIDENCE_MISSING" in result.reasons
    assert result.review_ready is False
    assert result.publish_readiness is False


def test_kp_d02_partial_evidence_does_not_crash_evaluation(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    evidence_id = _seed_business_evidence(repository)
    candidate = _business_candidate(
        repository, evidence_refs=[evidence_id]
    ).model_copy(
        update={"evidence_refs": [evidence_id, "EVD-MISSING"]}
    )

    result = KnowledgeEvaluationService(repository).evaluate(candidate)

    assert result.evidence_status == "PARTIAL"
    assert "EVIDENCE_PARTIAL" in result.reasons
    assert result.review_ready is True
    assert result.publish_readiness is False


def test_kp_d02_external_source_traceability_is_validated(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    candidate = _seed_external_candidate(repository)

    result = KnowledgeEvaluationService(repository).evaluate(candidate)

    assert result.source_valid is True
    assert result.evidence_status == "VALID"
    assert result.review_ready is True


def test_kp_d02_external_missing_source_is_blocked(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    evidence_id = _seed_business_evidence(repository, "EVD-EXT")
    candidate = KnowledgeCandidate(
        candidate_id="EXT-MISSING",
        candidate_source_type="EXTERNAL_SOURCE",
        object_type="CONCEPT",
        title="Missing source",
        content="Cannot publish without the source document.",
        device_type="SSD",
        scope=["health"],
        evidence_refs=[evidence_id],
        source_refs=[source_ref_key("MISSING", "R1")],
        extraction_version="kp-d02-test",
        confidence=0.5,
        producer="KNOWLEDGE_EXTRACTION",
        contract_version="knowledge-candidate/v1",
        created_at=CREATED_AT,
    )

    result = KnowledgeEvaluationService(repository).evaluate(candidate)

    assert result.source_valid is False
    assert "SOURCE_UNAVAILABLE" in result.reasons
    assert result.publish_readiness is False


def test_kp_d02_exact_published_match_is_duplicate(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    evidence_id = _seed_business_evidence(repository)
    candidate = _business_candidate(repository, evidence_refs=[evidence_id])
    _published(repository)

    result = KnowledgeEvaluationService(repository).evaluate(candidate)

    assert result.duplicate_status == "DUPLICATE"
    assert result.duplicate_object_ids == ["KO-001"]
    assert result.conflict_status == "NONE"
    assert result.publish_readiness is False
    assert "DUPLICATE" in result.reasons


def test_kp_d02_same_semantic_object_different_active_content_is_conflict(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    evidence_id = _seed_business_evidence(repository)
    candidate = _business_candidate(repository, evidence_refs=[evidence_id])
    _published(
        repository,
        content="Do not cache small writes; persist every write immediately.",
    )

    result = KnowledgeEvaluationService(repository).evaluate(candidate)

    assert result.duplicate_status == "POSSIBLE_DUPLICATE"
    assert result.conflict_status == "CONFLICT"
    assert result.conflict_object_ids == ["KO-001"]
    assert result.review_ready is True
    assert result.publish_readiness is False


def test_kp_d02_deprecated_object_does_not_create_active_conflict(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    evidence_id = _seed_business_evidence(repository)
    candidate = _business_candidate(repository, evidence_refs=[evidence_id])
    _published(
        repository,
        content="Older different recommendation.",
        status="DEPRECATED",
    )

    result = KnowledgeEvaluationService(repository).evaluate(candidate)

    assert result.duplicate_status == "POSSIBLE_DUPLICATE"
    assert result.conflict_status == "NONE"
    assert result.publish_readiness is False


def test_kp_d02_unrelated_published_object_is_new(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    evidence_id = _seed_business_evidence(repository)
    candidate = _business_candidate(repository, evidence_refs=[evidence_id])
    _published(repository, title="Wear leveling")

    result = KnowledgeEvaluationService(repository).evaluate(candidate)

    assert result.duplicate_status == "NEW"
    assert result.conflict_status == "NONE"
    assert result.publish_readiness is True


def test_kp_d02_evaluation_never_mutates_candidate_or_published_object(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    evidence_id = _seed_business_evidence(repository)
    candidate = _business_candidate(repository, evidence_refs=[evidence_id])
    _published(repository, content="Different active content.")
    candidate_before = repository.load(
        "knowledge/production/candidates/BC-001.json", required=True
    )
    published_before = repository.load(
        "knowledge/production/published/KO-001.json", required=True
    )

    result = KnowledgeEvaluationService(repository).evaluate(candidate)

    assert result.conflict_status == "CONFLICT"
    assert repository.load(
        "knowledge/production/candidates/BC-001.json", required=True
    ) == candidate_before
    assert repository.load(
        "knowledge/production/published/KO-001.json", required=True
    ) == published_before
    assert candidate.status == "CANDIDATE"


def test_kp_d02_evaluation_history_is_not_silently_overwritten(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    evidence_id = _seed_business_evidence(repository)
    candidate_without = _business_candidate(repository, evidence_refs=[])
    service = KnowledgeEvaluationService(repository)

    first = service.evaluate(candidate_without)
    candidate_with = candidate_without.model_copy(
        update={"evidence_refs": [evidence_id]}
    )
    second = service.evaluate(candidate_with)

    assert first.evaluation_id != second.evaluation_id
    assert len(
        repository.list(
            "knowledge/production/evaluations/BC-001"
        )
    ) == 2


def test_kp_d02_evaluate_by_id_round_trip(tmp_path: Path) -> None:
    repository = JsonArtifactRepository(tmp_path)
    evidence_id = _seed_business_evidence(repository)
    _business_candidate(repository, evidence_refs=[evidence_id])

    result = KnowledgeEvaluationService(repository).evaluate_by_id("BC-001")

    assert result.candidate_id == "BC-001"
    assert result.review_ready is True


def test_kp_d02_invalid_persisted_candidate_contract_fails_closed(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    repository.save(
        "knowledge/production/candidates/BAD.json",
        {"candidate_id": "BAD", "unexpected": True},
    )

    with pytest.raises(
        KnowledgeEvaluationError,
        match="KNOWLEDGE_CONTRACT_INVALID",
    ):
        KnowledgeEvaluationService(repository).evaluate_by_id("BAD")
