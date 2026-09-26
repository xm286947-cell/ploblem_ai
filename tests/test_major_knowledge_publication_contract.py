from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from knowledge_production import (
    KnowledgePublishError,
    MajorPublicationIntakeAdapter,
)
from repositories import JsonArtifactRepository
from quality_knowledge.major_cases.repository import MajorKnowledgeRepository
from services.major_knowledge_publication import (
    MajorKnowledgePublicationAdapter,
    MajorKnowledgePublicationError,
    PUBLICATION_CONTRACT,
)


def _major_repo(tmp_path: Path) -> MajorKnowledgeRepository:
    return MajorKnowledgeRepository(tmp_path / "major.sqlite3", tmp_path / "attachments")


def _confirmed_major(tmp_path: Path) -> tuple[MajorKnowledgeRepository, dict, dict]:
    repo = _major_repo(tmp_path)
    case = repo.create_case("控制器重大问题", "G1", domain="PLC")
    event = repo.upsert_event(
        case["case_id"],
        standard_itr="ITR-RCM-R2-001",
        internal_event_key="ITR-RCM-R2-001",
        title="控制器重大问题事件",
    )
    repo.update_case_status(case["case_id"], "ACTIVE")
    link = repo.add_source_link(
        case["case_id"],
        event["event_id"],
        {
            "record_id": "ROW-RCM-R2-001",
            "source_type": "ITR",
            "source_system": "BUSINESS_DB",
        },
        standard_itr=event["standard_itr"],
        role="CURRENT_EVENT",
        status="LINKED",
    )
    for entry_type, content in (
        ("ISSUE_FACT", "控制器周期性重启"),
        ("ROOT_CAUSE", "CAN 队列缺少流控"),
        ("ACTION", "增加队列水位保护"),
        ("VERIFICATION", "回归测试通过"),
    ):
        repo.add_entry(
            case["case_id"],
            entry_type,
            content,
            assertion_kind="FACT",
            origin="HUMAN",
            status="CONFIRMED",
            event_id=event["event_id"],
            evidence=[
                {
                    "source_link_id": link["source_link_id"],
                    "locator": entry_type.lower(),
                    "excerpt": content,
                }
            ],
        )
    return repo, case, event


def _publication(tmp_path: Path):
    repo, case, event = _confirmed_major(tmp_path)
    publication = MajorKnowledgePublicationAdapter(repo).build_publication(
        event["event_id"]
    )
    return repo, case, event, publication


def test_p01_contract_shape_and_p02_public_ref_binding(tmp_path: Path) -> None:
    _, _, event, publication = _publication(tmp_path)

    assert publication.contract_version == PUBLICATION_CONTRACT
    assert publication.producer == "MAJOR_ISSUE"
    assert publication.publication_status == "SUBMITTED"
    assert publication.source.source_type == "MAJOR_EVENT"
    assert publication.source.source_id == event["event_id"]
    assert publication.source.business_ref == "ITR-RCM-R2-001"
    assert publication.major_case_ref == "MAJOR_CASE:ITR-RCM-R2-001"


def test_p03_p04_revision_and_common_evidence_traceability(tmp_path: Path) -> None:
    _, _, _, publication = _publication(tmp_path)

    assert publication.major_confirmed_revision.startswith("KENTRY-")
    assert "KREV-" in publication.major_confirmed_revision
    assert publication.publication_revision.startswith("MJP-")
    assert publication.evidence_refs
    assert len(publication.evidence_refs) == len(publication.evidence_bindings)
    for evidence in publication.evidence_bindings:
        assert evidence.contract_version == "common-evidence/v1.0"
        assert evidence.source["source_id"]
        assert evidence.source["source_version"]
        assert evidence.locator["anchor"].startswith("entry:")
        assert ":revision:" in evidence.locator["anchor"]
        assert evidence.excerpt
        assert evidence.content_hash


def test_p05_same_revision_is_idempotent_and_p06_conflict_is_rejected(
    tmp_path: Path,
) -> None:
    _, _, _, publication = _publication(tmp_path)
    repository = JsonArtifactRepository(tmp_path / "knowledge")
    intake = MajorPublicationIntakeAdapter(repository)

    first = intake.submit(publication)
    replay = intake.submit(publication)
    assert first.status == "SUBMITTED"
    assert replay.status == "IDEMPOTENT_REPLAY"
    assert replay.candidate_ids == first.candidate_ids

    changed = publication.model_copy(
        update={"source_refs": [*publication.source_refs, "ADDITIVE-TRACE"]}
    )
    with pytest.raises(Exception) as conflict:
        intake.submit(changed)
    assert getattr(conflict.value, "code", "") == "PUBLICATION_IDENTITY_CONFLICT"


def test_p07_unconfirmed_major_fails_closed(tmp_path: Path) -> None:
    repo = _major_repo(tmp_path)
    case = repo.create_case("待确认重大问题", "G1")
    event = repo.upsert_event(
        case["case_id"], standard_itr="ITR-RCM-R2-PENDING", internal_event_key="pending"
    )
    repo.update_case_status(case["case_id"], "ACTIVE")
    repo.add_entry(
        case["case_id"],
        "ISSUE_FACT",
        "待人工确认事实",
        assertion_kind="FACT",
        origin="AI",
        status="PENDING",
        event_id=event["event_id"],
    )

    with pytest.raises(MajorKnowledgePublicationError) as error:
        MajorKnowledgePublicationAdapter(repo).build_publication(event["event_id"])
    assert error.value.code == "MAJOR_NOT_CONFIRMED"


def test_p08_knowledge_review_and_publish_are_not_bypassed(tmp_path: Path) -> None:
    # Keep the Major and Knowledge stores independent; the public publication
    # object is the only hand-off used below.
    major_repo, _, event = _confirmed_major(tmp_path / "major")
    publication = MajorKnowledgePublicationAdapter(major_repo).build_publication(
        event["event_id"]
    )
    knowledge = JsonArtifactRepository(tmp_path / "knowledge")
    submission = MajorPublicationIntakeAdapter(knowledge).submit(publication)

    with pytest.raises(KnowledgePublishError, match="PUBLISH_NOT_CONFIRMED"):
        from knowledge_production import KnowledgePublishService

        KnowledgePublishService(knowledge).publish(
            submission.candidate_ids[0],
            published_by="pmo",
            published_at=datetime.now(timezone.utc),
        )


def test_p09_new_major_revision_creates_new_publication_path(tmp_path: Path) -> None:
    repo, case, event = _confirmed_major(tmp_path)
    adapter = MajorKnowledgePublicationAdapter(repo)
    before = adapter.build_publication(event["event_id"])
    entry = next(
        item
        for item in repo.entries(case["case_id"])
        if item["entry_type"] == "ACTION"
    )
    repo.revise_entry(
        entry["entry_id"],
        "增加队列水位保护并验证异常恢复",
        "CONFIRMED",
        "reviewer",
        "new confirmed revision",
    )
    after = adapter.build_publication(event["event_id"])

    assert before.major_confirmed_revision != after.major_confirmed_revision
    assert before.publication_revision != after.publication_revision
    assert before.idempotency_key != after.idempotency_key
    assert {
        candidate.candidate_id for candidate in before.knowledge_candidates
    }.isdisjoint(
        candidate.candidate_id for candidate in after.knowledge_candidates
    )


def test_p10_cross_domain_boundary_is_contract_only() -> None:
    producer = Path("services/major_knowledge_publication.py").read_text()
    consumer = Path("knowledge_production/major_publication.py").read_text()

    assert "knowledge_production" not in producer
    assert "MajorKnowledgeRepository" in producer
    assert "SELECT " not in producer
    assert "sqlite3" not in consumer
    assert "MajorKnowledgeRepository" not in consumer
    assert "BusinessCandidateIntakeService" in consumer


def test_binding_returns_release_reference_without_mutating_knowledge(tmp_path: Path) -> None:
    _, _, _, publication = _publication(tmp_path)
    candidate_id = publication.knowledge_candidates[0].candidate_id
    binding = MajorKnowledgePublicationAdapter.bind_knowledge_release(
        publication,
        candidate_id=candidate_id,
        knowledge_object_ref="KO-MAJOR-R2-001",
        knowledge_object_version=1,
        knowledge_release_version="KP-STORAGE-RC1-VALIDATION-001",
    )
    assert binding.major_publication_revision == publication.publication_revision
    assert binding.knowledge_release_version == "KP-STORAGE-RC1-VALIDATION-001"
