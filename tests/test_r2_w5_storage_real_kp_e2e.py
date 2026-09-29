from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from knowledge_production import (
    KnowledgeEvaluationService,
    KnowledgeExtractionService,
    KnowledgePublishService,
    KnowledgeReleaseService,
    KnowledgeReviewService,
    SourceDocument,
    StructuredDocument,
    StructuredTextBlock,
)
from products.storage_rc1.storage_life.knowledge_release import KnowledgeReleaseConsumer
from repositories import JsonArtifactRepository


ROOT = Path(__file__).resolve().parents[1]


def _require_real_provider() -> None:
    if os.environ.get("R2_STORAGE_REAL_KP_E2E", "").strip().lower() not in {
        "1", "true", "yes", "on"
    }:
        pytest.skip("R2 Storage Real Knowledge smoke is opt-in")
    missing = [
        name
        for name in ("DASHSCOPE_BASE_URL", "DASHSCOPE_API_KEY")
        if not os.environ.get(name)
    ]
    if missing:
        pytest.fail("REAL_PROVIDER_CONFIG_MISSING:" + ",".join(missing))


def test_r2_storage_real_provider_knowledge_production_to_consumer(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _require_real_provider()

    repo_root = tmp_path / "knowledge-repository"
    repo = JsonArtifactRepository(repo_root)
    now = datetime.now(timezone.utc)
    source_id = "R2-REAL-KP-SYNTHETIC"
    source_version = "1.0"
    source_text = (
        "Percentage Used is an estimate of the percentage of NVM subsystem "
        "life consumed. This diagnostic value can be monitored to understand "
        "SSD endurance consumption."
    )
    block_hash = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    document_hash = hashlib.sha256(
        ("source:" + source_text).encode("utf-8")
    ).hexdigest()

    source = SourceDocument(
        source_id=source_id,
        source_version=source_version,
        publisher="R2 Product Test",
        title="Synthetic NVMe endurance excerpt",
        version=source_version,
        revision=source_version,
        document_type="TEXT_FIXTURE",
        source_ref="synthetic://r2-real-kp",
        local_cache_ref="synthetic/r2-real-kp.txt",
        original_file_name="r2-real-kp.txt",
        content_hash=document_hash,
        language="en",
        retrieval_status="PARSED",
        source_status="ACTIVE",
        created_at=now,
    )
    structured = StructuredDocument(
        source_id=source_id,
        source_version=source_version,
        parse_status="PARSED",
        page_count=1,
        blocks=[
            StructuredTextBlock(
                source_id=source_id,
                source_version=source_version,
                page=1,
                section="SMART / Health",
                source_text=source_text,
                source_anchor="page:1",
                content_hash=block_hash,
            )
        ],
        warnings=[],
    )
    base = f"knowledge/source_documents/{source_id}/{source_version}"
    repo.save(f"{base}/source_document.json", source.model_dump(mode="json"))
    repo.save(
        f"{base}/structured_document.json",
        structured.model_dump(mode="json"),
    )

    extraction = KnowledgeExtractionService.from_project(
        ROOT,
        model_config_path=ROOT / "config" / "runtime" / "model.yaml",
        runtime_db_path=tmp_path / "runtime.db",
        environ=dict(os.environ),
    )
    candidates = extraction.extract(
        source,
        structured,
        requested_topics=["Percentage Used"],
    )
    assert candidates, "REAL_AI_EXTRACTION_NO_CANDIDATE"

    selected = next(
        (
            item
            for item in candidates
            if "percentage used" in item.title.lower()
        ),
        candidates[0],
    )
    evaluation = KnowledgeEvaluationService(repo).evaluate(selected)
    assert evaluation.review_ready is True
    assert evaluation.publish_readiness is True

    review = KnowledgeReviewService(repo).confirm(
        selected.candidate_id,
        evaluation.evaluation_id,
        reviewed_by="r2-real-provider-smoke",
        reviewed_at=now,
        review_note="Synthetic source, real provider, automated gate.",
    )
    assert review.review_status.value == "CONFIRMED"

    obj = KnowledgePublishService(repo).publish(
        selected.candidate_id,
        published_by="r2-real-provider-smoke",
        published_at=now,
    )

    release_version = "R2-REAL-KP-SMOKE-R1"
    manifest = KnowledgeReleaseService(repo).build(
        release_version,
        created_at=now,
    )
    release_dir = repo.resolve(
        f"knowledge/production/releases/{release_version}"
    )
    monkeypatch.setenv(
        "STORAGE_KNOWLEDGE_RELEASE_DIR",
        str(release_dir),
    )

    status = KnowledgeReleaseConsumer.current().status()
    assert status["available"] is True
    assert status["knowledge_release_version"] == release_version

    result = KnowledgeReleaseConsumer.current().query(
        "Percentage Used",
        top_k=5,
    )
    assert any(
        item.get("object_id") == obj.object_id
        for item in result["results"]
    )

    print("PROVIDER_CONNECTIVITY=PASS")
    print("REAL_AI_EXTRACTION=PASS")
    print("CANDIDATE_EVIDENCE=PASS")
    print("HUMAN_REVIEW_SIMULATION=PASS")
    print("FORMAL_KNOWLEDGE_RELEASE=PASS")
    print("STORAGE_CONSUMER_NEW_RELEASE=PASS")
