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
    KnowledgeEvaluationService,
    KnowledgeObjectStatus,
    KnowledgePublishError,
    KnowledgePublishService,
    KnowledgeReviewError,
    KnowledgeReviewService,
    source_ref_key,
)
from repositories import JsonArtifactRepository
from runtime.contracts import EvidenceLocator, EvidenceReference, SourceRef


REVIEWED_AT = datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc)
PUBLISHED_AT = datetime(2026, 9, 23, 9, 30, tzinfo=timezone.utc)


def _seed_business_evidence(repository: JsonArtifactRepository) -> str:
    evidence = EvidenceReference(
        evidence_id="EVD-D03-001",
        source=SourceRef(
            source_id="CASE-001",
            source_type="HISTORICAL_CASE",
            revision="V1",
            content_hash=None,
            fingerprint="historical-case:CASE-001:V1",
            uri=None,
            metadata={"contract_version": "historical-case/v1"},
        ),
        locator=EvidenceLocator(
            type="BUSINESS_RECORD",
            value={"case_id": "CASE-001", "field": "solution"},
        ),
        excerpt="合并高频小写可降低介质写放大。",
        metadata={"evidence_status": "BOUND"},
    )
    repository.save(
        "knowledge/production/evidence/EVD-D03-001.json",
        evidence.model_dump(mode="json"),
    )
    return evidence.evidence_id


def _business_candidate(
    repository: JsonArtifactRepository,
    *,
    candidate_id: str = "BC-D03-001",
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
            "business_source_version": "V1",
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
            "created_at": REVIEWED_AT.isoformat(),
            "producer": "historical-case/v1",
            "contract_version": "knowledge-candidate/v1",
            "metadata": {},
        }
    )


def _external_candidate(repository: JsonArtifactRepository) -> KnowledgeCandidate:
    text = "Percentage Used estimates NVM subsystem life consumed."
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
        candidate_id="EXT-D03-001",
        candidate_source_type="EXTERNAL_SOURCE",
        object_type="DIAGNOSTIC",
        title="Percentage Used",
        content="Percentage Used estimates NVM subsystem life consumed.",
        device_type="SSD",
        scope=["health"],
        evidence_refs=[evidence.evidence_id],
        source_refs=[source_ref_key("NVME", "2.0d")],
        extraction_version="kp-d03-test",
        confidence=0.95,
        producer="KNOWLEDGE_EXTRACTION",
        contract_version="knowledge-candidate/v1",
        created_at=REVIEWED_AT,
    )
    return service.save_candidate(candidate)


def _evaluate(repository: JsonArtifactRepository, candidate: KnowledgeCandidate):
    return KnowledgeEvaluationService(repository).evaluate(candidate)


def _confirm(repository: JsonArtifactRepository, candidate: KnowledgeCandidate):
    evaluation = _evaluate(repository, candidate)
    review = KnowledgeReviewService(repository).confirm(
        candidate.candidate_id,
        evaluation.evaluation_id,
        reviewed_by="reviewer-a",
        reviewed_at=REVIEWED_AT,
        review_note="confirmed",
    )
    return evaluation, review


def test_kp_d03_human_confirm_creates_immutable_review_snapshot(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    evidence_id = _seed_business_evidence(repository)
    candidate = _business_candidate(repository, evidence_refs=[evidence_id])
    original = repository.load(
        "knowledge/production/candidates/BC-D03-001.json", required=True
    )

    evaluation, review = _confirm(repository, candidate)

    assert evaluation.review_ready is True
    assert review.action == "CONFIRM"
    assert review.review_status == "CONFIRMED"
    assert review.reviewed_by == "reviewer-a"
    assert review.effective_evaluation_id == evaluation.evaluation_id
    assert repository.load(review.effective_snapshot_ref, required=True) == original
    assert repository.load(
        "knowledge/production/candidates/BC-D03-001.json", required=True
    ) == original


def test_kp_d03_human_edit_preserves_original_candidate_and_audit(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    evidence_id = _seed_business_evidence(repository)
    candidate = _business_candidate(repository, evidence_refs=[evidence_id])
    evaluation = _evaluate(repository, candidate)
    original = repository.load(
        "knowledge/production/candidates/BC-D03-001.json", required=True
    )

    review = KnowledgeReviewService(repository).edit(
        candidate.candidate_id,
        evaluation.evaluation_id,
        {"content": "Human-reviewed merge-write guidance."},
        reviewed_by="reviewer-b",
        reviewed_at=REVIEWED_AT,
        review_note="clarified wording",
    )
    effective = KnowledgeReviewService(repository).load_effective_candidate(review)

    assert review.action == "EDIT"
    assert review.review_status == "CONFIRMED"
    assert review.edit_fields == ["content"]
    assert review.input_evaluation_id == evaluation.evaluation_id
    assert review.effective_evaluation_id != evaluation.evaluation_id
    assert effective.content == "Human-reviewed merge-write guidance."
    assert repository.load(
        "knowledge/production/candidates/BC-D03-001.json", required=True
    ) == original


def test_kp_d03_reject_blocks_publish(tmp_path: Path) -> None:
    repository = JsonArtifactRepository(tmp_path)
    evidence_id = _seed_business_evidence(repository)
    candidate = _business_candidate(repository, evidence_refs=[evidence_id])
    evaluation = _evaluate(repository, candidate)

    review = KnowledgeReviewService(repository).reject(
        candidate.candidate_id,
        evaluation.evaluation_id,
        reviewed_by="reviewer-c",
        reviewed_at=REVIEWED_AT,
        review_note="not reusable knowledge",
    )
    assert review.review_status == "REJECTED"

    with pytest.raises(KnowledgePublishError, match="PUBLISH_NOT_CONFIRMED"):
        KnowledgePublishService(repository).publish(
            candidate.candidate_id,
            published_by="publisher",
            published_at=PUBLISHED_AT,
        )


def test_kp_d03_confirm_requires_review_ready_candidate(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    candidate = _business_candidate(repository, evidence_refs=[])
    evaluation = _evaluate(repository, candidate)
    assert evaluation.review_ready is False

    with pytest.raises(
        KnowledgeReviewError,
        match="CANDIDATE_NOT_REVIEW_READY",
    ):
        KnowledgeReviewService(repository).confirm(
            candidate.candidate_id,
            evaluation.evaluation_id,
            reviewed_by="reviewer",
            reviewed_at=REVIEWED_AT,
        )


def test_kp_d03_edit_cannot_change_provenance_fields(tmp_path: Path) -> None:
    repository = JsonArtifactRepository(tmp_path)
    evidence_id = _seed_business_evidence(repository)
    candidate = _business_candidate(repository, evidence_refs=[evidence_id])
    evaluation = _evaluate(repository, candidate)

    with pytest.raises(
        KnowledgeReviewError,
        match="REVIEW_EDIT_FIELD_NOT_ALLOWED",
    ):
        KnowledgeReviewService(repository).edit(
            candidate.candidate_id,
            evaluation.evaluation_id,
            {"business_source_id": "CASE-OTHER"},
            reviewed_by="reviewer",
            reviewed_at=REVIEWED_AT,
        )


def test_kp_d03_publish_creates_active_object_and_is_idempotent(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    evidence_id = _seed_business_evidence(repository)
    candidate = _business_candidate(repository, evidence_refs=[evidence_id])
    _confirm(repository, candidate)

    service = KnowledgePublishService(repository)
    first = service.publish(
        candidate.candidate_id,
        published_by="publisher",
        published_at=PUBLISHED_AT,
    )
    second = service.publish(
        candidate.candidate_id,
        published_by="publisher",
        published_at=PUBLISHED_AT,
    )

    assert first == second
    assert first.status == "ACTIVE"
    assert first.object_version == 1
    assert first.contract_version == "knowledge-object/v1"
    assert first.evidence_refs == [evidence_id]
    assert repository.load(
        f"knowledge/production/published/{first.object_id}.json",
        required=True,
    )["object_version"] == 1
    assert len(
        repository.list(
            f"knowledge/production/published_versions/{first.object_id}"
        )
    ) == 1


def test_kp_d03_publish_rechecks_evidence_and_fails_closed(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    evidence_id = _seed_business_evidence(repository)
    candidate = _business_candidate(repository, evidence_refs=[evidence_id])
    _confirm(repository, candidate)
    repository.resolve(
        f"knowledge/production/evidence/{evidence_id}.json"
    ).unlink()

    with pytest.raises(KnowledgePublishError, match="EVIDENCE_MISSING"):
        KnowledgePublishService(repository).publish(
            candidate.candidate_id,
            published_by="publisher",
            published_at=PUBLISHED_AT,
        )


def test_kp_d03_publish_rechecks_external_source_and_fails_closed(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    candidate = _external_candidate(repository)
    _confirm(repository, candidate)
    repository.resolve(
        "knowledge/source_documents/NVME/2.0d/source_document.json"
    ).unlink()

    with pytest.raises(KnowledgePublishError, match="SOURCE_UNAVAILABLE"):
        KnowledgePublishService(repository).publish(
            candidate.candidate_id,
            published_by="publisher",
            published_at=PUBLISHED_AT,
        )


def test_kp_d03_unresolved_conflict_blocks_publish(tmp_path: Path) -> None:
    repository = JsonArtifactRepository(tmp_path)
    evidence_id = _seed_business_evidence(repository)
    candidate = _business_candidate(repository, evidence_refs=[evidence_id])
    repository.save(
        "knowledge/production/published/KO-OTHER.json",
        {
            "object_id": "KO-OTHER",
            "object_version": 1,
            "contract_version": "knowledge-object/v1",
            "status": "ACTIVE",
            "candidate_id": "OTHER",
            "review_id": "R-OTHER",
            "evaluation_id": "E-OTHER",
            "candidate_source_type": "BUSINESS",
            "business_source_type": "HISTORICAL_CASE",
            "business_source_id": "CASE-X",
            "business_source_version": "V1",
            "object_type": "SOLUTION",
            "title": "Merge small writes",
            "summary": None,
            "content": "Persist every small write immediately.",
            "device_type": "GENERIC",
            "scope": ["storage_lifetime"],
            "conditions": [],
            "limitations": [],
            "tags": [],
            "evidence_refs": ["E-X"],
            "source_refs": ["HISTORICAL_CASE:CASE-X@V1"],
            "producer": "manual",
            "published_by": "tester",
            "published_at": PUBLISHED_AT.isoformat(),
        },
    )
    evaluation, _ = _confirm(repository, candidate)
    assert evaluation.conflict_status == "CONFLICT"

    with pytest.raises(KnowledgePublishError, match="UNRESOLVED_CONFLICT"):
        KnowledgePublishService(repository).publish(
            candidate.candidate_id,
            published_by="publisher",
            published_at=PUBLISHED_AT,
        )


def test_kp_d03_exact_duplicate_blocks_publish(tmp_path: Path) -> None:
    repository = JsonArtifactRepository(tmp_path)
    evidence_id = _seed_business_evidence(repository)
    candidate = _business_candidate(repository, evidence_refs=[evidence_id])
    repository.save(
        "knowledge/production/published/KO-OTHER.json",
        {
            "object_id": "KO-OTHER",
            "object_version": 1,
            "contract_version": "knowledge-object/v1",
            "status": "ACTIVE",
            "candidate_id": "OTHER",
            "review_id": "R-OTHER",
            "evaluation_id": "E-OTHER",
            "candidate_source_type": "BUSINESS",
            "business_source_type": "HISTORICAL_CASE",
            "business_source_id": "CASE-X",
            "business_source_version": "V1",
            "object_type": "SOLUTION",
            "title": candidate.title,
            "summary": candidate.summary,
            "content": candidate.content,
            "device_type": candidate.device_type,
            "scope": candidate.scope,
            "conditions": candidate.conditions,
            "limitations": candidate.limitations,
            "tags": candidate.tags,
            "evidence_refs": ["E-X"],
            "source_refs": ["HISTORICAL_CASE:CASE-X@V1"],
            "producer": "manual",
            "published_by": "tester",
            "published_at": PUBLISHED_AT.isoformat(),
        },
    )
    evaluation, _ = _confirm(repository, candidate)
    assert evaluation.duplicate_status == "DUPLICATE"

    with pytest.raises(KnowledgePublishError, match="DUPLICATE_KNOWLEDGE"):
        KnowledgePublishService(repository).publish(
            candidate.candidate_id,
            published_by="publisher",
            published_at=PUBLISHED_AT,
        )


def test_kp_d03_human_edit_can_create_next_object_version(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    evidence_id = _seed_business_evidence(repository)
    candidate = _business_candidate(repository, evidence_refs=[evidence_id])
    evaluation, _ = _confirm(repository, candidate)
    publisher = KnowledgePublishService(repository)
    v1 = publisher.publish(
        candidate.candidate_id,
        published_by="publisher",
        published_at=PUBLISHED_AT,
    )

    review = KnowledgeReviewService(repository).edit(
        candidate.candidate_id,
        evaluation.evaluation_id,
        {"content": "Reviewed v2 guidance for merge-write handling."},
        reviewed_by="reviewer-b",
        reviewed_at=datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc),
        review_note="v2",
    )
    assert review.review_status == "CONFIRMED"

    v2 = publisher.publish(
        candidate.candidate_id,
        published_by="publisher",
        published_at=datetime(2026, 9, 23, 10, 30, tzinfo=timezone.utc),
    )

    assert v2.object_id == v1.object_id
    assert v2.object_version == 2
    assert v1.content != v2.content
    history = repository.list(
        f"knowledge/production/published_versions/{v1.object_id}"
    )
    assert [path.name for path in history] == [
        "v000001.json",
        "v000002.json",
    ]
    assert repository.load(history[0], required=True)["content"] == v1.content
    assert repository.load(history[1], required=True)["content"] == v2.content


def test_kp_d03_object_lifecycle_active_stale_deprecated(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    evidence_id = _seed_business_evidence(repository)
    candidate = _business_candidate(repository, evidence_refs=[evidence_id])
    _confirm(repository, candidate)
    service = KnowledgePublishService(repository)
    active = service.publish(
        candidate.candidate_id,
        published_by="publisher",
        published_at=PUBLISHED_AT,
    )
    stale = service.transition_status(
        active.object_id,
        KnowledgeObjectStatus.STALE,
        changed_by="owner",
        changed_at=datetime(2026, 9, 24, 9, 0, tzinfo=timezone.utc),
    )
    deprecated = service.transition_status(
        active.object_id,
        KnowledgeObjectStatus.DEPRECATED,
        changed_by="owner",
        changed_at=datetime(2026, 9, 25, 9, 0, tzinfo=timezone.utc),
    )

    assert active.object_version == 1 and active.status == "ACTIVE"
    assert stale.object_version == 2 and stale.status == "STALE"
    assert deprecated.object_version == 3 and deprecated.status == "DEPRECATED"
    with pytest.raises(
        KnowledgePublishError,
        match="KNOWLEDGE_STATE_TRANSITION_INVALID",
    ):
        service.transition_status(
            active.object_id,
            KnowledgeObjectStatus.ACTIVE,
            changed_by="owner",
            changed_at=datetime(2026, 9, 26, 9, 0, tzinfo=timezone.utc),
        )
