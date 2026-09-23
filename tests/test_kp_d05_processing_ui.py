from __future__ import annotations

import hashlib
import inspect
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

import knowledge_production.web as web_module
from knowledge_production import (
    BusinessCandidateIntakeService,
    KnowledgeProcessingService,
    SourceDocument,
    create_processing_app,
)
from repositories import JsonArtifactRepository
from runtime.contracts import EvidenceLocator, EvidenceReference, SourceRef


CREATED_AT = datetime(2026, 9, 23, 11, 0, tzinfo=timezone.utc)


def _repository(tmp_path: Path) -> JsonArtifactRepository:
    return JsonArtifactRepository(tmp_path / "repo")


def _seed_source(repository: JsonArtifactRepository) -> None:
    digest = hashlib.sha256(b"official-pdf").hexdigest()
    source = SourceDocument(
        source_id="NVME",
        source_version="2.0d",
        publisher="NVM Express",
        title="NVM Express Base Specification",
        version="2.0d",
        revision="2.0d",
        document_type="PDF",
        official_url="https://nvmexpress.org/specification",
        source_ref="seed/03_SSD_NVME/NVM-Express-Base-2.0d.pdf",
        local_cache_ref="knowledge/source_documents/NVME/2.0d/original.pdf",
        original_file_name="NVM-Express-Base-2.0d.pdf",
        content_hash=digest,
        language="en",
        retrieval_status="PARSED",
        source_status="ACTIVE",
        created_at=CREATED_AT,
    )
    repository.save(
        "knowledge/source_documents/NVME/2.0d/source_document.json",
        source.model_dump(mode="json"),
    )


def _seed_business_candidate(
    repository: JsonArtifactRepository,
    *,
    candidate_id: str = "BC-UI-001",
    with_evidence: bool = True,
) -> str:
    evidence_refs: list[str] = []
    if with_evidence:
        evidence = EvidenceReference(
            evidence_id=f"EVD-{candidate_id}",
            source=SourceRef(
                source_id="CASE-001",
                source_type="HISTORICAL_CASE",
                revision="V3",
                content_hash=None,
                fingerprint="historical-case:CASE-001:V3",
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
            f"knowledge/production/evidence/{evidence.evidence_id}.json",
            evidence.model_dump(mode="json"),
        )
        evidence_refs = [evidence.evidence_id]

    BusinessCandidateIntakeService(repository).intake(
        {
            "candidate_id": candidate_id,
            "candidate_source_type": "BUSINESS",
            "business_source_type": "HISTORICAL_CASE",
            "business_source_id": "CASE-001",
            "business_source_version": "V3",
            "object_type": "SOLUTION",
            "title": "Merge small writes",
            "content": "Merge or cache small writes to reduce write amplification.",
            "device_type": "GENERIC",
            "scope": ["storage_lifetime"],
            "conditions": ["high-frequency small writes"],
            "limitations": ["requires durability design"],
            "tags": ["write_amplification"],
            "source_refs": [],
            "evidence_refs": evidence_refs,
            "confidence": 0.9,
            "created_at": CREATED_AT.isoformat(),
            "producer": "historical-case/v1",
            "contract_version": "knowledge-candidate/v1",
            "metadata": {},
        }
    )
    return candidate_id


def _client(repository: JsonArtifactRepository) -> TestClient:
    return TestClient(create_processing_app(repository.root))


def test_kp_d05_four_views_are_visually_and_semantically_distinct(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    _seed_source(repository)
    _seed_business_candidate(repository)
    client = _client(repository)

    sources = client.get("/knowledge-production/sources")
    candidates = client.get("/knowledge-production/candidates")
    reviews = client.get("/knowledge-production/reviews")
    published = client.get("/knowledge-production/published")

    assert sources.status_code == 200
    assert "Source Document" in sources.text
    assert "资料本身不是正式 Knowledge Object" in sources.text
    assert "Knowledge Candidate" in candidates.text
    assert "Human Gate" in reviews.text
    assert "Published Knowledge" in published.text


def test_kp_d05_source_ui_shows_source_version_and_parse_status(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    _seed_source(repository)

    response = _client(repository).get("/knowledge-production/sources")

    assert "NVM Express Base Specification" in response.text
    assert "2.0d" in response.text
    assert "PARSED" in response.text


def test_kp_d05_candidate_list_shows_business_provenance(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    _seed_business_candidate(repository)

    response = _client(repository).get("/knowledge-production/candidates")

    assert "BC-UI-001" in response.text
    assert "BUSINESS" in response.text
    assert "HISTORICAL_CASE" in response.text
    assert "NOT_EVALUATED" in response.text


def test_kp_d05_candidate_detail_read_has_no_evaluation_side_effect(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    candidate_id = _seed_business_candidate(repository)
    before = repository.list(
        f"knowledge/production/evaluations/{candidate_id}"
    )

    response = _client(repository).get(
        f"/knowledge-production/candidates/{candidate_id}"
    )

    after = repository.list(
        f"knowledge/production/evaluations/{candidate_id}"
    )
    assert response.status_code == 200
    assert "Evaluation / Dedup / Conflict" in response.text
    assert "执行 Evaluation" in response.text
    assert before == after == []


def test_kp_d05_evaluate_confirm_publish_full_ui_flow(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    candidate_id = _seed_business_candidate(repository)
    app = create_processing_app(repository.root)
    client = TestClient(app)

    evaluated = client.post(
        f"/knowledge-production/candidates/{candidate_id}/evaluate",
        follow_redirects=False,
    )
    assert evaluated.status_code == 303
    detail = app.state.knowledge_processing_service.get_candidate_detail(
        candidate_id
    )
    evaluation = detail["latest_evaluation"]
    assert evaluation is not None
    assert evaluation.evidence_status == "VALID"
    assert evaluation.duplicate_status == "NEW"
    assert evaluation.conflict_status == "NONE"

    confirmed = client.post(
        f"/knowledge-production/candidates/{candidate_id}/confirm",
        data={
            "evaluation_id": evaluation.evaluation_id,
            "reviewed_by": "reviewer-ui",
            "review_note": "UI confirm",
        },
        follow_redirects=False,
    )
    assert confirmed.status_code == 303

    published = client.post(
        f"/knowledge-production/candidates/{candidate_id}/publish",
        data={"published_by": "publisher-ui"},
        follow_redirects=False,
    )
    assert published.status_code == 303
    page = client.get("/knowledge-production/published")
    assert "Merge small writes" in page.text
    assert "ACTIVE" in page.text


def test_kp_d05_edit_uses_domain_review_and_preserves_original_candidate(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    candidate_id = _seed_business_candidate(repository)
    app = create_processing_app(repository.root)
    client = TestClient(app)
    service: KnowledgeProcessingService = app.state.knowledge_processing_service
    evaluation = service.evaluate(candidate_id)
    original = repository.load(
        f"knowledge/production/candidates/{candidate_id}.json",
        required=True,
    )

    response = client.post(
        f"/knowledge-production/candidates/{candidate_id}/edit",
        data={
            "evaluation_id": evaluation.evaluation_id,
            "reviewed_by": "reviewer-ui",
            "title": "Merge small writes",
            "content": "Human reviewed merge-write guidance.",
            "review_note": "clarified",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert repository.load(
        f"knowledge/production/candidates/{candidate_id}.json",
        required=True,
    ) == original
    detail = service.get_candidate_detail(candidate_id)
    assert detail["latest_review"].action == "EDIT"
    effective = service.reviews.load_effective_candidate(
        detail["latest_review"]
    )
    assert effective.content == "Human reviewed merge-write guidance."


def test_kp_d05_reject_then_publish_is_blocked_by_domain_gate(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    candidate_id = _seed_business_candidate(repository)
    app = create_processing_app(repository.root)
    client = TestClient(app)
    evaluation = app.state.knowledge_processing_service.evaluate(candidate_id)

    rejected = client.post(
        f"/knowledge-production/candidates/{candidate_id}/reject",
        data={
            "evaluation_id": evaluation.evaluation_id,
            "reviewed_by": "reviewer-ui",
            "review_note": "not reusable",
        },
        follow_redirects=False,
    )
    assert rejected.status_code == 303

    publish = client.post(
        f"/knowledge-production/candidates/{candidate_id}/publish",
        data={"published_by": "publisher-ui"},
        follow_redirects=False,
    )
    assert publish.status_code == 409
    assert "PUBLISH_NOT_CONFIRMED" in publish.text


def test_kp_d05_invalid_evidence_cannot_be_confirmed_in_ui(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    candidate_id = _seed_business_candidate(
        repository,
        candidate_id="BC-UI-MISSING",
        with_evidence=False,
    )
    app = create_processing_app(repository.root)
    client = TestClient(app)
    evaluation = app.state.knowledge_processing_service.evaluate(candidate_id)
    assert evaluation.evidence_status == "INVALID"

    response = client.post(
        f"/knowledge-production/candidates/{candidate_id}/confirm",
        data={
            "evaluation_id": evaluation.evaluation_id,
            "reviewed_by": "reviewer-ui",
            "review_note": "",
        },
        follow_redirects=False,
    )

    assert response.status_code == 409
    assert "CANDIDATE_NOT_REVIEW_READY" in response.text


def test_kp_d05_review_ui_is_audit_view_not_candidate_overwrite(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    candidate_id = _seed_business_candidate(repository)
    service = KnowledgeProcessingService(repository)
    evaluation = service.evaluate(candidate_id)
    service.confirm(
        candidate_id,
        evaluation.evaluation_id,
        reviewed_by="reviewer-ui",
        reviewed_at=CREATED_AT,
        review_note="confirmed",
    )

    page = _client(repository).get("/knowledge-production/reviews")

    assert "CONFIRM" in page.text
    assert "CONFIRMED" in page.text
    assert "reviewer-ui" in page.text
    assert candidate_id in page.text


def test_kp_d05_published_view_excludes_unpublished_candidates(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    _seed_business_candidate(repository)

    page = _client(repository).get("/knowledge-production/published")

    assert "Merge small writes" not in page.text
    assert "暂无 Published Knowledge" in page.text


def test_kp_d05_web_layer_does_not_implement_repository_state_machine() -> None:
    source = inspect.getsource(web_module)

    assert ".load(" not in source
    assert ".save(" not in source
    assert "KnowledgeEvaluationService" not in source
    assert "KnowledgeReviewService" not in source
    assert "KnowledgePublishService" not in source
    assert "KnowledgeProcessingService" in source
