from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from knowledge_production import (
    BusinessCandidateIntakeService,
    BusinessEvidenceIntakeService,
    KnowledgeEvaluationService,
    KnowledgePublishError,
    KnowledgePublishService,
    KnowledgeReviewService,
)
from repositories import JsonArtifactRepository

from storage_life.knowledge_suggestions import (
    PublicKnowledgeSuggestionService,
    SuggestionError,
)


SOURCE_ID = "public-source-001"
REVISION = "Rev2.1"
LOCATOR = {"page": 4, "section": "Endurance", "chunk_id": "chunk-04-02"}
CITATION_ID = "citation-123"
SOURCE_TEXT = "Program endurance is rated for the stated operating conditions."


def _service(tmp_path, *, classification="PUBLIC", citation_revision=REVISION, locator=LOCATOR):
    repository = JsonArtifactRepository(tmp_path)
    citation = {
        "citation_id": CITATION_ID,
        "source_id": SOURCE_ID,
        "source_revision": citation_revision,
        "locator": locator,
        "text": SOURCE_TEXT,
    }
    source_response = {
        "source": {
            "source_id": SOURCE_ID,
            "classification": classification,
            "revision": REVISION,
            "media_type": "text/markdown",
            "source_uri": "https://vendor.example/public.pdf",
            "content_hash": "a" * 64,
        },
        "revisions": [{"revision_id": REVISION}],
    }
    service = PublicKnowledgeSuggestionService(
        repository,
        evidence_intake=BusinessEvidenceIntakeService(repository),
        candidate_intake=BusinessCandidateIntakeService(repository),
        resolve_citation=lambda citation_id, mode: {**citation, "mode": mode},
        resolve_source=lambda source_id, mode: {**source_response, "mode": mode},
    )
    return service, repository


def _create(service, *, mode="LIVE"):
    return service.create({
        "title": "Program endurance condition",
        "suggested_object_type": "FACT",
        "summary": "Draft from a selected public citation.",
        "content": {
            "definition": "Program endurance is stated for specified conditions.",
            "engineering_meaning": "",
            "applicability": "See source conditions.",
            "limitations": ["Do not generalize outside the cited conditions."],
        },
        "source_refs": [{
            "source_id": SOURCE_ID,
            "revision": REVISION,
            "locator": LOCATOR,
            "citation_id": CITATION_ID,
            "source_uri": "https://vendor.example/public.pdf",
            "immutable_identity": "a" * 64,
        }],
        "origin_mode": mode,
        "origin_kind": "SEARCH",
        "origin_query": "program endurance",
    })


def test_live_suggestion_handoff_reuses_candidate_and_evidence_stores(tmp_path):
    service, repository = _service(tmp_path)
    suggestion = _create(service)

    ready = service.validate(suggestion["suggestion_id"], mode="LIVE")
    assert ready["status"] == "READY_FOR_HANDOFF"
    result = service.handoff(suggestion["suggestion_id"], mode="LIVE")
    retry_result = service.handoff(suggestion["suggestion_id"], mode="LIVE")

    assert result["status"] == "ACCEPTED_AS_CANDIDATE"
    assert retry_result["candidate_id"] == result["candidate_id"]
    assert result["candidate_id"].startswith("PKC-")
    assert result["evidence_status"] == "RESOLVED"
    candidate = repository.load(
        f"knowledge/production/candidates/{result['candidate_id']}.json",
        required=True,
    )
    assert candidate["candidate_source_type"] == "BUSINESS"
    assert candidate["metadata"]["origin_workspace"] == "public-knowledge"
    assert candidate["metadata"]["source_refs"][0]["citation_id"] == CITATION_ID
    evidence = repository.load(
        f"knowledge/production/evidence/{candidate['evidence_refs'][0]}.json",
        required=True,
    )
    assert evidence["source"]["revision"] == REVISION
    assert evidence["locator"]["value"]["page"] == 4
    assert evidence["excerpt"] == SOURCE_TEXT
    assert repository.list("knowledge/production/published") == []


def test_existing_candidate_detail_displays_public_knowledge_origin(tmp_path):
    service, repository = _service(tmp_path)
    suggestion = _create(service)
    service.validate(suggestion["suggestion_id"], mode="LIVE")
    result = service.handoff(suggestion["suggestion_id"], mode="LIVE")
    from knowledge_production import create_processing_app

    page = TestClient(create_processing_app(repository.root)).get(
        result["candidate_url"]
    )
    assert page.status_code == 200
    assert "Origin: Public Knowledge" in page.text
    assert "Public Knowledge Suggestion" in page.text
    assert CITATION_ID in page.text
    assert SOURCE_ID in page.text


def test_suggestion_http_routes_and_storage_workspace_entry(monkeypatch, tmp_path):
    from fastapi import FastAPI
    import storage_life.public_knowledge as public_knowledge

    service, _ = _service(tmp_path)
    monkeypatch.setattr(public_knowledge, "_suggestion_service", lambda base_url=None: service)
    app = FastAPI()
    app.include_router(public_knowledge.router)
    client = TestClient(app)

    created = client.post("/api/public-knowledge/suggestions", json={
        "title": "Fixture suggestion",
        "suggested_object_type": "FACT",
        "summary": "Display-only demo",
        "content": {"definition": "Example draft"},
        "source_refs": [{
            "source_id": SOURCE_ID,
            "revision": REVISION,
            "locator": LOCATOR,
            "citation_id": CITATION_ID,
        }],
        "origin_mode": "FIXTURE_REPLAY",
        "origin_kind": "SEARCH",
    })
    assert created.status_code == 201
    suggestion_id = created.json()["suggestion_id"]
    assert created.json()["status"] == "DEMO_ONLY"
    assert client.get("/api/public-knowledge/suggestions").json()["suggestions"][0]["suggestion_id"] == suggestion_id
    assert client.post(f"/api/public-knowledge/suggestions/{suggestion_id}/handoff?mode=FIXTURE_REPLAY").status_code == 409

    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / "storage_life" / "index.html").read_text(encoding="utf-8")
    assert 'data-page="pksuggestions"' in html
    assert "生成知识候选草稿" in html
    assert "送入现有 Knowledge Production" in html


def test_fixture_suggestion_is_demo_only_and_cannot_handoff(tmp_path):
    service, repository = _service(tmp_path)
    suggestion = _create(service, mode="FIXTURE_REPLAY")
    assert suggestion["status"] == "DEMO_ONLY"

    try:
        service.validate(suggestion["suggestion_id"], mode="FIXTURE_REPLAY")
    except SuggestionError as exc:
        assert exc.code == "DEMO_ONLY_BLOCKED"
    else:
        raise AssertionError("Fixture suggestion must not enter formal KP")

    assert repository.list("knowledge/production/candidates") == []


def test_live_handoff_fails_closed_on_nonpublic_source_and_changed_locator(tmp_path):
    private, private_repo = _service(tmp_path / "private", classification="INTERNAL")
    private_suggestion = _create(private)
    try:
        private.validate(private_suggestion["suggestion_id"], mode="LIVE")
    except SuggestionError as exc:
        assert exc.code == "SOURCE_NOT_PUBLIC"
    else:
        raise AssertionError("Non-PUBLIC source must be blocked")
    assert private.get(private_suggestion["suggestion_id"])["status"] == "HANDOFF_FAILED"
    assert private_repo.list("knowledge/production/candidates") == []

    changed, changed_repo = _service(
        tmp_path / "changed", locator={"page": 5, "section": "Other", "chunk_id": "changed"}
    )
    changed_suggestion = _create(changed)
    try:
        changed.validate(changed_suggestion["suggestion_id"], mode="LIVE")
    except SuggestionError as exc:
        assert exc.code == "SOURCE_LOCATOR_MISMATCH"
    else:
        raise AssertionError("Changed citation locator must be blocked")
    assert changed.get(changed_suggestion["suggestion_id"])["status"] == "EVIDENCE_UNRESOLVED"
    assert changed_repo.list("knowledge/production/candidates") == []


def test_existing_publish_gate_blocks_public_candidate_after_evidence_drift(tmp_path):
    service, repository = _service(tmp_path)
    suggestion = _create(service)
    service.validate(suggestion["suggestion_id"], mode="LIVE")
    result = service.handoff(suggestion["suggestion_id"], mode="LIVE")
    candidate_id = result["candidate_id"]

    evaluation = KnowledgeEvaluationService(repository).evaluate_by_id(candidate_id)
    KnowledgeReviewService(repository).confirm(
        candidate_id,
        evaluation.evaluation_id,
        reviewed_by="wave2-test",
        reviewed_at=datetime.now(timezone.utc),
    )
    evidence_path = f"knowledge/production/evidence/{repository.load(f'knowledge/production/candidates/{candidate_id}.json')['evidence_refs'][0]}.json"
    evidence = repository.load(evidence_path, required=True)
    evidence["locator"]["value"].pop("page")
    repository.save(evidence_path, evidence)

    try:
        KnowledgePublishService(repository).publish(
            candidate_id,
            published_by="wave2-test",
            published_at=datetime.now(timezone.utc),
        )
    except KnowledgePublishError as exc:
        assert exc.code == "EVIDENCE_MISSING"
    else:
        raise AssertionError("Publish must fail closed after evidence drift")
    assert repository.list("knowledge/production/published") == []


def test_existing_publish_gate_blocks_public_candidate_after_revision_mismatch(tmp_path):
    service, repository = _service(tmp_path)
    suggestion = _create(service)
    service.validate(suggestion["suggestion_id"], mode="LIVE")
    result = service.handoff(suggestion["suggestion_id"], mode="LIVE")
    candidate_id = result["candidate_id"]

    evaluation = KnowledgeEvaluationService(repository).evaluate_by_id(candidate_id)
    KnowledgeReviewService(repository).confirm(
        candidate_id,
        evaluation.evaluation_id,
        reviewed_by="wave2-test",
        reviewed_at=datetime.now(timezone.utc),
    )
    candidate = repository.load(
        f"knowledge/production/candidates/{candidate_id}.json", required=True
    )
    evidence_path = f"knowledge/production/evidence/{candidate['evidence_refs'][0]}.json"
    evidence = repository.load(evidence_path, required=True)
    evidence["source"]["revision"] = "Rev9.9"
    repository.save(evidence_path, evidence)

    try:
        KnowledgePublishService(repository).publish(
            candidate_id,
            published_by="wave2-test",
            published_at=datetime.now(timezone.utc),
        )
    except KnowledgePublishError as exc:
        assert exc.code == "EVIDENCE_MISSING"
    else:
        raise AssertionError("Publish must fail closed after revision mismatch")
    assert repository.list("knowledge/production/published") == []
