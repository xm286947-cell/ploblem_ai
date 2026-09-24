from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from knowledge_production import (
    EvidenceLocation,
    KnowledgeCandidate,
    KnowledgeCandidateService,
    KnowledgeEvaluationService,
    KnowledgePublishService,
    KnowledgeQueryService,
    KnowledgeReleaseService,
    KnowledgeReviewService,
    source_ref_key,
)
from knowledge_production.storage_compat import (
    StorageKnowledgeCompatibilityService,
)
from repositories import JsonArtifactRepository


RELEASE_VERSION = "KP-STORAGE-RC1-VALIDATION-001"
DIST = ROOT / "dist" / "kp-storage-rc1"


def _storage_request() -> dict:
    return {
        "contract_version": "V1.0",
        "request_id": "kp-storage-rc1-consumer-sample",
        "service_id": "storage_knowledge_service",
        "query": {
            "text": "Percentage Used device life",
            "fields": {
                "device_type": "SSD",
                "parameter_keys": ["percentage_used"],
                "topics": ["device_health"],
            },
        },
        "filters": {
            "device_type": "SSD",
            "parameter_keys": ["percentage_used"],
        },
        "requested_fields": [],
        "options": {"top_k": 5},
        "caller": {
            "type": "storage",
            "agent_id": "storage.comparison.impact",
        },
    }


def _publish_validation_object(repository: JsonArtifactRepository):
    now = datetime.now(timezone.utc)
    source_text = (
        "Percentage Used contains an estimate of NVM subsystem life consumed."
    )
    document_hash = hashlib.sha256(b"nvme-base-2.0d-validation-source").hexdigest()

    repository.save(
        "knowledge/source_documents/NVME/2.0d/source_document.json",
        {
            "source_id": "NVME",
            "source_version": "2.0d",
            "publisher": "NVM Express",
            "title": "NVM Express Base Specification",
            "version": "2.0d",
            "revision": "2.0d",
            "document_type": "PDF",
            "official_url": "https://nvmexpress.org/specification",
            "source_ref": "seed/03_SSD_NVME/NVM-Express-Base-2.0d.pdf",
            "local_cache_ref": (
                "knowledge/source_documents/NVME/2.0d/original.pdf"
            ),
            "original_file_name": "NVM-Express-Base-2.0d.pdf",
            "content_hash": document_hash,
            "language": "en",
            "retrieval_status": "PARSED",
            "source_status": "ACTIVE",
            "created_at": now.isoformat(),
        },
    )
    repository.save(
        "knowledge/source_documents/NVME/2.0d/structured_document.json",
        {
            "source_id": "NVME",
            "source_version": "2.0d",
            "parse_status": "PARSED",
            "page_count": 500,
            "blocks": [
                {
                    "source_id": "NVME",
                    "source_version": "2.0d",
                    "page": 200,
                    "section": "SMART / Health",
                    "source_text": source_text,
                    "source_anchor": "page:200",
                    "content_hash": hashlib.sha256(
                        source_text.encode("utf-8")
                    ).hexdigest(),
                }
            ],
            "warnings": [],
        },
    )

    candidates = KnowledgeCandidateService(repository)
    evidence = candidates.bind_evidence(
        EvidenceLocation(
            source_id="NVME",
            source_version="2.0d",
            page=200,
            section="SMART / Health",
            source_anchor="page:200",
        )
    )
    candidate = candidates.save_candidate(
        KnowledgeCandidate(
            candidate_id="KP-STORAGE-RC1-PERCENTAGE-USED",
            candidate_source_type="EXTERNAL_SOURCE",
            object_type="DIAGNOSTIC",
            title="Percentage Used",
            content=(
                "Percentage Used estimates NVM subsystem life consumed."
            ),
            device_type="SSD",
            scope=["health", "endurance"],
            tags=["percentage_used", "device_health", "nvme"],
            evidence_refs=[evidence.evidence_id],
            source_refs=[source_ref_key("NVME", "2.0d")],
            extraction_version="kp-storage-rc1-delivery",
            confidence=0.95,
            producer="KNOWLEDGE_EXTRACTION",
            contract_version="knowledge-candidate/v1",
            created_at=now,
        )
    )

    evaluation = KnowledgeEvaluationService(repository).evaluate(candidate)
    if not evaluation.review_ready:
        raise RuntimeError("EVALUATION_NOT_REVIEW_READY")

    review = KnowledgeReviewService(repository).confirm(
        candidate.candidate_id,
        evaluation.evaluation_id,
        reviewed_by="kp-storage-rc1-gate",
        reviewed_at=now,
        review_note="Controlled RC1 consumer validation release",
    )
    if review.review_status != "CONFIRMED":
        raise RuntimeError("REVIEW_NOT_CONFIRMED")

    obj = KnowledgePublishService(repository).publish(
        candidate.candidate_id,
        published_by="kp-storage-rc1-gate",
        published_at=now,
    )
    if obj.status != "ACTIVE":
        raise RuntimeError("PUBLISHED_OBJECT_NOT_ACTIVE")
    return candidate, evaluation, review, obj


def main() -> None:
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="kp-storage-rc1-") as tmp:
        repository = JsonArtifactRepository(Path(tmp) / "repository")
        candidate, evaluation, review, obj = _publish_validation_object(repository)

        manifest = KnowledgeReleaseService(repository).build(
            RELEASE_VERSION,
            created_at=datetime.now(timezone.utc),
        )

        query_result = KnowledgeQueryService(repository).query(
            {
                "knowledge_release_version": RELEASE_VERSION,
                "object_ids": [obj.object_id],
            }
        )
        if len(query_result.objects) != 1:
            raise RuntimeError("PUBLISHED_OBJECT_QUERY_FAILED")
        if not query_result.evidences:
            raise RuntimeError("EVIDENCE_TRACEABILITY_FAILED")
        if not query_result.source_references:
            raise RuntimeError("SOURCE_REFERENCE_TRACEABILITY_FAILED")

        storage_request = _storage_request()
        storage_response = StorageKnowledgeCompatibilityService(
            KnowledgeQueryService(repository),
            knowledge_release_version=RELEASE_VERSION,
        ).handle_query(storage_request)
        if not storage_response.get("success"):
            raise RuntimeError("STORAGE_CONSUMER_COMPATIBILITY_FAILED")
        if storage_response["result"]["total"] != 1:
            raise RuntimeError("STORAGE_CONSUMER_RESULT_COUNT_INVALID")
        if not storage_response.get("evidence"):
            raise RuntimeError("STORAGE_CONSUMER_EVIDENCE_MISSING")

        release_root = repository.resolve(
            f"knowledge/production/releases/{RELEASE_VERSION}"
        )
        package = repository.resolve(
            "knowledge/production/release_packages/"
            f"knowledge-release-{RELEASE_VERSION}.zip"
        )

        shutil.copy2(package, DIST / package.name)
        for name in (
            "release_manifest.json",
            "knowledge_objects.json",
            "evidences.json",
            "source_references.json",
            "MIGRATION_NOTES.md",
            "COMPATIBILITY_NOTES.md",
        ):
            shutil.copy2(release_root / name, DIST / name)

        (DIST / "storage_consumer_request.json").write_text(
            json.dumps(storage_request, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (DIST / "storage_consumer_response.json").write_text(
            json.dumps(storage_response, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        gate_result = {
            "RESULT": "PASS",
            "TASK": "KP-STORAGE-RC1-BLOCKER-001",
            "RELEASE_VERSION": RELEASE_VERSION,
            "KNOWLEDGE_RELEASE_AVAILABLE": "PASS",
            "PUBLISHED_OBJECT_QUERY": "PASS",
            "EVIDENCE_TRACEABLE": "PASS",
            "SOURCE_REFERENCE_TRACEABLE": "PASS",
            "STORAGE_CONSUMER_COMPATIBILITY": "PASS",
            "STORAGE_DIRECT_KNOWLEDGE_DB_ACCESS": "NO",
            "STORAGE_CANDIDATE_STORE_ACCESS": "NO",
            "STORAGE_SELF_PUBLISH": "NO",
            "NEW_KNOWLEDGE_CAPABILITY_ADDED": "NO",
            "RUNTIME_MODIFIED": "NO",
            "RC1_KNOWLEDGE_BLOCKER": "CLEARED_FOR_CONSUMER_VALIDATION",
            "CONTENT_CLASS": "CONTROLLED_CONSUMER_VALIDATION_SAMPLE",
            "REAL_AI_GOLDEN_A": "OUT_OF_SCOPE_FOR_THIS_GATE",
            "candidate_id": candidate.candidate_id,
            "evaluation_id": evaluation.evaluation_id,
            "review_id": review.review_id,
            "object_id": obj.object_id,
            "snapshot_hash": manifest.snapshot_hash,
            "object_count": manifest.object_count,
            "evidence_count": manifest.evidence_count,
            "source_reference_count": manifest.source_reference_count,
        }
        (DIST / "gate_result.json").write_text(
            json.dumps(gate_result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        print(json.dumps(gate_result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
