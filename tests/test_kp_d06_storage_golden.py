from __future__ import annotations

import hashlib
import inspect
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, Field

import knowledge_production.storage_compat as storage_compat_module
from knowledge_production import (
    EvidenceLocation,
    HistoricalCaseCandidateProducer,
    KnowledgeCandidate,
    KnowledgeCandidateService,
    KnowledgeEvaluationService,
    KnowledgePublishService,
    KnowledgeReleaseService,
    KnowledgeReviewService,
    create_knowledge_api_app,
    source_ref_key,
)
from repositories import JsonArtifactRepository
from services.historical_case_contract import HistoricalCaseConsumerService


NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)


class FrozenConsumerModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class FrozenEvidenceSource(FrozenConsumerModel):
    source_id: str
    source_title: str = ""
    publisher: str = ""
    url: str = ""
    file_name: str = ""
    page: int | None = Field(default=None, ge=1)
    section: str = ""
    raw_text: str = ""
    evidence_type: str = ""
    evidence_id: str = ""


class FrozenKnowledgeItem(FrozenConsumerModel):
    knowledge_id: str
    title: str
    knowledge_type: str = ""
    summary: str
    content_excerpt: str = ""
    source: FrozenEvidenceSource
    applicable_device_types: list[str] = Field(default_factory=list)
    applicable_parameter_keys: list[str] = Field(default_factory=list)
    verification_status: str
    confidence: float | None = Field(default=None, ge=0, le=1)


def _storage_request(
    *,
    request_id: str,
    text: str,
    device_type: str = "",
    parameter_keys: list[str] | None = None,
    topics: list[str] | None = None,
    limit: int = 5,
) -> dict:
    return {
        "contract_version": "V1.0",
        "request_id": request_id,
        "service_id": "storage_knowledge_service",
        "query": {
            "text": text,
            "fields": {
                "device_type": device_type,
                "parameter_keys": parameter_keys or [],
                "topics": topics or [],
            },
        },
        "filters": {
            "device_type": device_type,
            "parameter_keys": parameter_keys or [],
        },
        "requested_fields": [],
        "options": {"top_k": limit},
        "caller": {
            "type": "storage",
            "agent_id": "storage.comparison.impact",
        },
    }


def _seed_historical_case(repository: JsonArtifactRepository) -> dict:
    case_id = "CASE-STORAGE-001"
    repository.save(
        f"knowledge/retrieval_docs/{case_id}.json",
        {
            "case_id": case_id,
            "title": "High-frequency small writes reduce eMMC life",
            "source_case_path": f"knowledge/enriched_case/{case_id}.json",
        },
    )
    repository.save(
        f"knowledge/enriched_case/{case_id}.json",
        {
            "case_id": case_id,
            "metadata": {
                "itr_id": "ITR-STORAGE-001",
                "report_filename": "ITR-STORAGE-001.pdf",
                "parse_status": "PARSED",
            },
            "business_context": {
                "product": "PLC",
                "device_type": "eMMC",
            },
            "problem": {
                "standard_description": (
                    "Frequent small writes accelerated storage wear."
                ),
                "phenomenon": [
                    "Device lifetime decreased faster than expected."
                ],
            },
            "analysis": {
                "root_cause": [
                    "Frequent small writes caused write amplification."
                ]
            },
            "solution": {
                "corrective_actions": [
                    "Merge or cache small writes to reduce write amplification."
                ],
                "verification_result": "VERIFIED",
            },
            "status": "ACTIVE",
        },
    )
    repository.save(
        f"knowledge/raw_evidence/{case_id}.json",
        {
            "case_id": case_id,
            "source_id": "ITR-STORAGE-001",
            "itr_id": "ITR-STORAGE-001",
            "report_filename": "ITR-STORAGE-001.pdf",
            "source_type": "REPORT",
            "sections": [
                {
                    "section": "root_cause",
                    "page_numbers": [3],
                    "raw_text": (
                        "Frequent small writes caused write amplification."
                    ),
                },
                {
                    "section": "solution",
                    "page_numbers": [5],
                    "raw_text": (
                        "Merge or cache small writes to reduce "
                        "write amplification."
                    ),
                },
            ],
        },
    )
    return HistoricalCaseConsumerService(repository).get_case(case_id)


def _publish_from_historical_case(
    repository: JsonArtifactRepository,
    historical_case: dict,
):
    candidate = HistoricalCaseCandidateProducer(
        repository
    ).intake_solution(historical_case, created_at=NOW)
    evaluation = KnowledgeEvaluationService(repository).evaluate(candidate)
    assert evaluation.review_ready is True
    review = KnowledgeReviewService(repository).confirm(
        candidate.candidate_id,
        evaluation.evaluation_id,
        reviewed_by="golden-b-reviewer",
        reviewed_at=NOW,
        review_note="Golden B confirmed",
    )
    assert review.review_status == "CONFIRMED"
    obj = KnowledgePublishService(repository).publish(
        candidate.candidate_id,
        published_by="golden-b-publisher",
        published_at=NOW,
    )
    return candidate, obj


def _publish_external_storage_knowledge(
    repository: JsonArtifactRepository,
):
    source_text = (
        "Percentage Used contains an estimate of NVM subsystem life consumed."
    )
    document_hash = hashlib.sha256(b"nvme-base-2.0d").hexdigest()
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
            "created_at": NOW.isoformat(),
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
            candidate_id="EXT-D06-PERCENTAGE-USED",
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
            extraction_version="kp-d06-fixture-only",
            confidence=0.95,
            producer="KNOWLEDGE_EXTRACTION",
            contract_version="knowledge-candidate/v1",
            created_at=NOW,
        )
    )
    evaluation = KnowledgeEvaluationService(repository).evaluate(candidate)
    KnowledgeReviewService(repository).confirm(
        candidate.candidate_id,
        evaluation.evaluation_id,
        reviewed_by="external-reviewer",
        reviewed_at=NOW,
    )
    return KnowledgePublishService(repository).publish(
        candidate.candidate_id,
        published_by="external-publisher",
        published_at=NOW,
    )


def test_kp_d06_storage_contract_consumes_released_external_knowledge(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    _publish_external_storage_knowledge(repository)
    KnowledgeReleaseService(repository).build(
        "KP-D06-EXT-R1",
        created_at=NOW,
    )
    client = TestClient(
        create_knowledge_api_app(
            str(repository.root),
            knowledge_release_version="KP-D06-EXT-R1",
        )
    )

    response = client.post(
        "/v1/knowledge/query",
        json=_storage_request(
            request_id="storage-ext-001",
            text="Percentage Used device life",
            device_type="SSD",
            parameter_keys=["percentage_used"],
            topics=["device_health"],
        ),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["contract_version"] == "V1.0"
    assert body["request_id"] == "storage-ext-001"
    assert body["service_id"] == "storage_knowledge_service"
    assert body["success"] is True
    assert body["result"]["total"] == 1

    item = FrozenKnowledgeItem.model_validate(body["result"]["results"][0])
    assert item.title == "Percentage Used"
    assert item.knowledge_type == "DIAGNOSTIC"
    assert item.applicable_device_types == ["SSD"]
    assert item.applicable_parameter_keys == ["percentage_used"]
    assert item.verification_status == "human_confirmed"
    assert item.source.source_id == "NVME"
    assert item.source.page == 200
    assert item.source.section == "SMART / Health"
    assert "Percentage Used" in item.source.raw_text


def test_kp_d06_storage_query_is_pinned_to_release_not_live_store(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    obj = _publish_external_storage_knowledge(repository)
    KnowledgeReleaseService(repository).build("R1", created_at=NOW)
    app = create_knowledge_api_app(
        str(repository.root),
        knowledge_release_version="R1",
    )

    changed = obj.model_copy(
        update={
            "object_version": 2,
            "content": "Changed after R1 and not yet released.",
        }
    )
    repository.save(
        f"knowledge/production/published/{changed.object_id}.json",
        changed.model_dump(mode="json"),
    )

    body = TestClient(app).post(
        "/v1/knowledge/query",
        json=_storage_request(
            request_id="storage-pin-001",
            text="Percentage Used",
            device_type="SSD",
        ),
    ).json()

    assert body["result"]["total"] == 1
    assert (
        body["result"]["results"][0]["content"]
        == "Percentage Used estimates NVM subsystem life consumed."
    )
    assert body["result"]["provider"] == "knowledge-production/R1"


def test_kp_d06_golden_b_historical_case_full_production_to_storage(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    historical_case = _seed_historical_case(repository)

    assert historical_case["contract_version"] == "historical-case/v1"
    assert historical_case["case_id"] == "CASE-STORAGE-001"
    assert historical_case["solution"]
    assert historical_case["evidence"]

    candidate, obj = _publish_from_historical_case(
        repository,
        historical_case,
    )
    assert candidate.candidate_source_type == "BUSINESS"
    assert candidate.business_source_type == "HISTORICAL_CASE"
    assert candidate.business_source_id == historical_case["case_id"]
    assert candidate.evidence_refs
    assert obj.status == "ACTIVE"

    KnowledgeReleaseService(repository).build(
        "KP-GOLDEN-B-R1",
        created_at=NOW,
    )
    client = TestClient(
        create_knowledge_api_app(
            str(repository.root),
            knowledge_release_version="KP-GOLDEN-B-R1",
        )
    )
    body = client.post(
        "/v1/knowledge/query",
        json=_storage_request(
            request_id="storage-golden-b",
            text="merge small writes write amplification",
            device_type="eMMC",
            topics=["solution"],
        ),
    ).json()

    assert body["success"] is True
    assert body["result"]["total"] == 1
    item = FrozenKnowledgeItem.model_validate(
        body["result"]["results"][0]
    )
    assert item.knowledge_id == obj.object_id
    assert item.knowledge_type == "SOLUTION"
    assert item.applicable_device_types == ["eMMC"]
    assert "write amplification" in item.content_excerpt
    assert item.source.source_id == "ITR-STORAGE-001"
    assert item.source.page in {3, 5}
    assert item.source.raw_text
    assert body["evidence"]
    assert (
        body["evidence"][0]["metadata"]["knowledge_release_version"]
        == "KP-GOLDEN-B-R1"
    )


def test_kp_d06_historical_case_adapter_does_not_copy_case_database(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    historical_case = _seed_historical_case(repository)
    candidate, _ = _publish_from_historical_case(
        repository,
        historical_case,
    )

    payload = repository.load(
        f"knowledge/production/candidates/{candidate.candidate_id}.json",
        required=True,
    )
    serialized = str(payload)
    assert "retrieval_docs/" not in serialized
    assert "enriched_case/" not in serialized
    assert "raw_evidence/" not in serialized
    assert payload["business_source_id"] == "CASE-STORAGE-001"


def test_kp_d06_storage_compat_layer_has_no_production_engine_duplication() -> None:
    source = inspect.getsource(storage_compat_module)

    assert "KnowledgeQueryService" in source
    assert "KnowledgeExtractionService" not in source
    assert "KnowledgeCandidateService" not in source
    assert "KnowledgeReviewService" not in source
    assert "KnowledgePublishService" not in source
    assert "Sqlite" not in source
    assert "vector" not in source.lower()


def test_kp_d06_contract_mismatch_fails_without_internal_leak(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    _publish_external_storage_knowledge(repository)
    KnowledgeReleaseService(repository).build("R1", created_at=NOW)
    client = TestClient(
        create_knowledge_api_app(
            str(repository.root),
            knowledge_release_version="R1",
        )
    )

    response = client.post(
        "/v1/knowledge/query",
        json={
            **_storage_request(
                request_id="bad-service",
                text="Percentage Used",
            ),
            "service_id": "unapproved-service",
        },
    )

    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "KNOWLEDGE_CONTRACT_INVALID"
    assert "internal_path" not in str(body)
    assert "sqlite" not in str(body).lower()
