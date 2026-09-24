from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from knowledge_production import (
    KNOWLEDGE_EVIDENCE_CONTRACT_VERSION,
    KNOWLEDGE_PUBLISH_CONTRACT_VERSION,
    KNOWLEDGE_REVIEW_CONTRACT_VERSION,
    KnowledgeReleaseService,
    PublicKnowledgeError,
    PublicKnowledgeService,
)
from repositories import JsonArtifactRepository


NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
RELEASE = "KNOWLEDGE_CAPABILITY_RELEASE_V0.3-HW-GOLDEN"
SOURCE_TEXT = (
    "Connector contact resistance increased after repeated vibration."
)


def _evidence_payload() -> dict:
    return {
        "contract_version": "knowledge-evidence/v1",
        "evidence_id": "EVD-HW-WORD-001",
        "source_document_id": "HW-WORD-001",
        "domain": "HARDWARE_CASE",
        "source_type": "WORD",
        "source_ref": "controlled://hardware-case/HW-WORD-001",
        "source_revision": "V1",
        "page": 2,
        "section": "Failure Analysis",
        "paragraph": "P3",
        "source_text": SOURCE_TEXT,
        "content_hash": hashlib.sha256(
            SOURCE_TEXT.encode("utf-8")
        ).hexdigest(),
        "revision": 1,
    }


def _candidate_payload(
    candidate_id: str = "HC-CAND-001",
    evidence_refs: list[str] | None = None,
) -> dict:
    return {
        "contract_version": "knowledge-candidate/v1",
        "candidate_id": candidate_id,
        "source_document_id": "HW-WORD-001",
        "domain": "HARDWARE_CASE",
        "object_type": "HARDWARE_CASE",
        "structured_content": {
            "phenomenon": "Intermittent communication loss",
            "failure_mechanism": "Connector contact degradation",
            "solution": "Improve connector retention and contact design",
            "tree_mapping": {
                "circuit_node": "Communication/Connector",
                "device_node": "Connector",
            },
        },
        "evidence_refs": evidence_refs or [],
        "status": "PENDING_REVIEW",
        "created_at": NOW.isoformat(),
        "revision": 1,
        "producer": "hardware-case/v1",
    }


def _seed_ready(service: PublicKnowledgeService) -> dict:
    evidence = service.intake_evidence(_evidence_payload())
    candidate = service.intake_candidate(
        _candidate_payload(evidence_refs=[evidence.evidence_id])
    )
    return {
        "evidence": evidence,
        "candidate": candidate,
    }


def test_contract_versions_are_frozen() -> None:
    assert KNOWLEDGE_EVIDENCE_CONTRACT_VERSION == "knowledge-evidence/v1"
    assert KNOWLEDGE_REVIEW_CONTRACT_VERSION == "knowledge-review/v1"
    assert KNOWLEDGE_PUBLISH_CONTRACT_VERSION == "knowledge-publish/v1"


def test_word_evidence_intake_is_public_and_idempotent(tmp_path: Path) -> None:
    service = PublicKnowledgeService(JsonArtifactRepository(tmp_path))

    first = service.intake_evidence(_evidence_payload())
    second = service.intake_evidence(_evidence_payload())

    assert first == second
    assert first.source_type == "WORD"
    assert first.page == 2
    assert first.section == "Failure Analysis"
    assert first.source_text == SOURCE_TEXT


def test_word_evidence_hash_mismatch_is_fail_closed(tmp_path: Path) -> None:
    service = PublicKnowledgeService(JsonArtifactRepository(tmp_path))
    payload = _evidence_payload()
    payload["content_hash"] = "0" * 64

    with pytest.raises(
        PublicKnowledgeError,
        match="EVIDENCE_CONTENT_HASH_MISMATCH",
    ):
        service.intake_evidence(payload)


def test_hardware_candidate_keeps_opaque_structured_content(
    tmp_path: Path,
) -> None:
    service = PublicKnowledgeService(JsonArtifactRepository(tmp_path))
    evidence = service.intake_evidence(_evidence_payload())

    candidate = service.intake_candidate(
        _candidate_payload(evidence_refs=[evidence.evidence_id])
    )

    assert candidate.domain == "HARDWARE_CASE"
    assert candidate.object_type == "HARDWARE_CASE"
    assert candidate.status == "PENDING_REVIEW"
    assert candidate.structured_content["tree_mapping"] == {
        "circuit_node": "Communication/Connector",
        "device_node": "Connector",
    }


def test_unreviewed_candidate_cannot_publish(tmp_path: Path) -> None:
    service = PublicKnowledgeService(JsonArtifactRepository(tmp_path))
    seeded = _seed_ready(service)

    with pytest.raises(PublicKnowledgeError, match="PUBLISH_NOT_CONFIRMED"):
        service.publish(
            {
                "contract_version": "knowledge-publish/v1",
                "candidate_id": seeded["candidate"].candidate_id,
                "idempotency_key": "HC-CAND-001-R1",
                "publisher": "hardware-publisher",
                "published_at": NOW.isoformat(),
                "revision": 1,
            }
        )


def test_confirm_publish_release_query_and_evidence_round_trip(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    service = PublicKnowledgeService(repository)
    seeded = _seed_ready(service)

    review = service.review(
        {
            "contract_version": "knowledge-review/v1",
            "candidate_id": seeded["candidate"].candidate_id,
            "action": "CONFIRM",
            "reviewer": "hardware-reviewer",
            "review_time": NOW.isoformat(),
            "review_comment": "Evidence verified",
            "revision": 1,
        }
    )
    assert review.review_status == "CONFIRMED"
    assert review.confirmed_value == seeded["candidate"].structured_content

    request = {
        "contract_version": "knowledge-publish/v1",
        "candidate_id": seeded["candidate"].candidate_id,
        "idempotency_key": "HC-CAND-001-R1",
        "publisher": "hardware-publisher",
        "published_at": NOW.isoformat(),
        "revision": 1,
    }
    first = service.publish(request)
    second = service.publish(request)
    assert first == second
    assert first.object.domain == "HARDWARE_CASE"
    assert first.object.object_type == "HARDWARE_CASE"

    manifest = KnowledgeReleaseService(repository).build(
        RELEASE,
        created_at=NOW,
    )
    assert manifest.object_count == 1

    result = service.query(
        {
            "contract_version": "knowledge-query/v1",
            "knowledge_release_version": RELEASE,
            "domain": "HARDWARE_CASE",
            "object_type": "HARDWARE_CASE",
        }
    )
    assert len(result.objects) == 1
    obj = result.objects[0]
    assert obj.knowledge_id == first.object.knowledge_id
    assert obj.content == seeded["candidate"].structured_content
    assert obj.candidate_ref == seeded["candidate"].candidate_id
    assert obj.evidence_refs == [seeded["evidence"].evidence_id]
    assert obj.knowledge_release_version == RELEASE

    evidence = service.resolve_evidence(
        RELEASE,
        seeded["evidence"].evidence_id,
    )
    assert evidence["source"]["source_id"] == "HW-WORD-001"
    assert evidence["source"]["source_type"] == "WORD"
    assert evidence["excerpt"] == SOURCE_TEXT


def test_human_edit_is_separate_from_original_ai_candidate(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    service = PublicKnowledgeService(repository)
    seeded = _seed_ready(service)
    original = seeded["candidate"].structured_content
    reviewed = {
        **original,
        "solution": "Add retention structure and verified contact margin.",
    }

    review = service.review(
        {
            "contract_version": "knowledge-review/v1",
            "candidate_id": seeded["candidate"].candidate_id,
            "action": "EDIT",
            "reviewer": "hardware-reviewer",
            "review_time": NOW.isoformat(),
            "review_comment": "Confirmed after schematic review",
            "reviewed_content": reviewed,
            "revision": 2,
        }
    )
    assert review.review_status == "CONFIRMED"
    assert review.reviewed_content == reviewed

    raw_candidate = repository.load(
        "knowledge/production/candidates/HC-CAND-001.json",
        required=True,
    )
    assert raw_candidate is not None
    assert json.loads(raw_candidate["content"]) == original

    published = service.publish(
        {
            "contract_version": "knowledge-publish/v1",
            "candidate_id": seeded["candidate"].candidate_id,
            "idempotency_key": "HC-CAND-001-R2",
            "publisher": "hardware-publisher",
            "published_at": NOW.isoformat(),
            "revision": 2,
        }
    )
    assert published.object.content == reviewed
