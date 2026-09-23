from __future__ import annotations

import hashlib
import inspect
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

import knowledge_production.release as release_module
from knowledge_production import (
    KnowledgeObject,
    KnowledgeQueryError,
    KnowledgeQueryService,
    KnowledgeReleaseError,
    KnowledgeReleaseService,
)
from repositories import JsonArtifactRepository
from runtime.contracts import EvidenceLocator, EvidenceReference, SourceRef


RELEASED_AT = datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc)


def _save_evidence(
    repository: JsonArtifactRepository,
    *,
    evidence_id: str,
    source_id: str,
    source_type: str,
    revision: str,
    excerpt: str,
    locator_type: str,
    locator_value: dict,
    content_hash: str | None = None,
) -> None:
    evidence = EvidenceReference(
        evidence_id=evidence_id,
        source=SourceRef(
            source_id=source_id,
            source_type=source_type,
            revision=revision,
            content_hash=content_hash,
            fingerprint=f"{source_type}:{source_id}:{revision}",
            uri=None,
            metadata={},
        ),
        locator=EvidenceLocator(
            type=locator_type,
            value=locator_value,
        ),
        excerpt=excerpt,
        metadata={"evidence_status": "BOUND"},
    )
    repository.save(
        f"knowledge/production/evidence/{evidence_id}.json",
        evidence.model_dump(mode="json"),
    )


def _seed_external(repository: JsonArtifactRepository) -> KnowledgeObject:
    digest = hashlib.sha256(b"nvme-base-2.0d").hexdigest()
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
            "local_cache_ref": "knowledge/source_documents/NVME/2.0d/original.pdf",
            "original_file_name": "NVM-Express-Base-2.0d.pdf",
            "content_hash": digest,
            "language": "en",
            "retrieval_status": "PARSED",
            "source_status": "ACTIVE",
            "created_at": RELEASED_AT.isoformat(),
        },
    )
    _save_evidence(
        repository,
        evidence_id="EVD-NVME-001",
        source_id="NVME",
        source_type="PDF",
        revision="2.0d",
        excerpt="Percentage Used contains a vendor specific estimate of life used.",
        locator_type="PAGE",
        locator_value={"page": 200, "section": "SMART / Health"},
        content_hash=digest,
    )
    obj = KnowledgeObject(
        object_id="KO-NVME-001",
        object_version=1,
        status="ACTIVE",
        candidate_id="EXT-NVME-001",
        review_id="KPR-NVME-001",
        evaluation_id="KPE-NVME-001",
        candidate_source_type="EXTERNAL_SOURCE",
        object_type="DIAGNOSTIC",
        title="Percentage Used",
        content="Percentage Used estimates consumed NVM subsystem life.",
        device_type="SSD",
        scope=["health", "endurance"],
        tags=["nvme", "device_health"],
        evidence_refs=["EVD-NVME-001"],
        source_refs=["NVME@2.0d"],
        producer="KNOWLEDGE_EXTRACTION",
        published_by="reviewer",
        published_at=RELEASED_AT,
    )
    repository.save(
        f"knowledge/production/published/{obj.object_id}.json",
        obj.model_dump(mode="json"),
    )
    return obj


def _seed_business(repository: JsonArtifactRepository) -> KnowledgeObject:
    _save_evidence(
        repository,
        evidence_id="EVD-CASE-001",
        source_id="CASE-001",
        source_type="HISTORICAL_CASE",
        revision="V3",
        excerpt="高频小写导致额外介质写入。",
        locator_type="BUSINESS_RECORD",
        locator_value={"case_id": "CASE-001", "field": "root_cause"},
    )
    obj = KnowledgeObject(
        object_id="KO-CASE-001",
        object_version=2,
        status="ACTIVE",
        candidate_id="BC-CASE-001",
        review_id="KPR-CASE-001",
        evaluation_id="KPE-CASE-001",
        candidate_source_type="BUSINESS",
        business_source_type="HISTORICAL_CASE",
        business_source_id="CASE-001",
        business_source_version="V3",
        object_type="SOLUTION",
        title="Merge small writes",
        content="Merge or cache small writes to reduce write amplification.",
        device_type="GENERIC",
        scope=["storage_lifetime"],
        tags=["write_amplification"],
        evidence_refs=["EVD-CASE-001"],
        source_refs=["HISTORICAL_CASE:CASE-001@V3"],
        producer="historical-case/v1",
        published_by="reviewer",
        published_at=RELEASED_AT,
    )
    repository.save(
        f"knowledge/production/published/{obj.object_id}.json",
        obj.model_dump(mode="json"),
    )
    return obj


def _seed_release(repository: JsonArtifactRepository):
    external = _seed_external(repository)
    business = _seed_business(repository)
    manifest = KnowledgeReleaseService(repository).build(
        "KP-2026.09.23-RC1",
        created_at=RELEASED_AT,
    )
    return external, business, manifest


def test_kp_d04_release_manifest_and_package_are_complete(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    _, _, manifest = _seed_release(repository)

    assert manifest.knowledge_release_version == "KP-2026.09.23-RC1"
    assert manifest.contract_version == "knowledge-query/v1"
    assert manifest.object_contract_version == "knowledge-object/v1"
    assert manifest.candidate_contract_version == "knowledge-candidate/v1"
    assert manifest.object_count == 2
    assert manifest.evidence_count == 2
    assert manifest.source_reference_count == 2
    assert manifest.snapshot_hash

    package = repository.resolve(
        "knowledge/production/release_packages/"
        "knowledge-release-KP-2026.09.23-RC1.zip"
    )
    assert package.is_file()
    with zipfile.ZipFile(package) as archive:
        assert sorted(archive.namelist()) == sorted(
            [
                "release_manifest.json",
                "knowledge_objects.json",
                "evidences.json",
                "source_references.json",
                "MIGRATION_NOTES.md",
                "COMPATIBILITY_NOTES.md",
            ]
        )


def test_kp_d04_query_returns_objects_evidence_sources_and_release_version(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    _seed_release(repository)

    result = KnowledgeQueryService(repository).query(
        {"knowledge_release_version": "KP-2026.09.23-RC1"}
    )

    assert result.contract_version == "knowledge-query/v1"
    assert result.knowledge_release_version == "KP-2026.09.23-RC1"
    assert {obj.object_id for obj in result.objects} == {
        "KO-NVME-001",
        "KO-CASE-001",
    }
    assert all(
        obj.knowledge_release_version == "KP-2026.09.23-RC1"
        for obj in result.objects
    )
    assert {item["evidence_id"] for item in result.evidences} == {
        "EVD-NVME-001",
        "EVD-CASE-001",
    }
    assert {item.source_ref for item in result.source_references} == {
        "NVME@2.0d",
        "HISTORICAL_CASE:CASE-001@V3",
    }
    assert result.unknowns_or_gaps == []


def test_kp_d04_external_source_reference_preserves_document_provenance(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    _seed_release(repository)

    source = KnowledgeQueryService(repository).get_source_reference(
        "KP-2026.09.23-RC1", "NVME@2.0d"
    )

    assert source.source_kind == "EXTERNAL_SOURCE_DOCUMENT"
    assert source.source_id == "NVME"
    assert source.source_version == "2.0d"
    assert source.publisher == "NVM Express"
    assert source.content_hash
    assert source.source_ref_uri.endswith("NVM-Express-Base-2.0d.pdf")


def test_kp_d04_business_source_reference_keeps_business_ownership(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    _seed_release(repository)

    source = KnowledgeQueryService(repository).get_source_reference(
        "KP-2026.09.23-RC1",
        "HISTORICAL_CASE:CASE-001@V3",
    )

    assert source.source_kind == "BUSINESS_OBJECT"
    assert source.source_type == "HISTORICAL_CASE"
    assert source.source_id == "CASE-001"
    assert source.source_version == "V3"
    assert source.content_hash is None


def test_kp_d04_evidence_query_round_trip(tmp_path: Path) -> None:
    repository = JsonArtifactRepository(tmp_path)
    _seed_release(repository)

    evidence = KnowledgeQueryService(repository).get_evidence(
        "KP-2026.09.23-RC1", "EVD-CASE-001"
    )

    assert evidence["source"]["source_id"] == "CASE-001"
    assert evidence["locator"]["type"] == "BUSINESS_RECORD"
    assert "高频小写" in evidence["excerpt"]


def test_kp_d04_query_filters_formal_objects_not_chunks(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    _seed_release(repository)

    result = KnowledgeQueryService(repository).query(
        {
            "knowledge_release_version": "KP-2026.09.23-RC1",
            "object_types": ["DIAGNOSTIC"],
            "device_types": ["SSD"],
            "tags": ["device_health"],
            "topic": "percentage used",
        }
    )

    assert [obj.object_id for obj in result.objects] == ["KO-NVME-001"]
    serialized = result.model_dump(mode="json")
    assert "chunks" not in serialized
    assert "top_n_chunks" not in serialized


def test_kp_d04_same_release_version_is_idempotent(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    _, _, first = _seed_release(repository)

    second = KnowledgeReleaseService(repository).build(
        "KP-2026.09.23-RC1",
        created_at=datetime(2026, 9, 23, 11, 0, tzinfo=timezone.utc),
    )

    assert second.snapshot_hash == first.snapshot_hash
    assert second.created_at == first.created_at


def test_kp_d04_same_release_version_changed_snapshot_is_blocked(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    external, _, _ = _seed_release(repository)
    changed = external.model_copy(
        update={
            "object_version": 2,
            "content": "Changed content after release.",
        }
    )
    repository.save(
        f"knowledge/production/published/{changed.object_id}.json",
        changed.model_dump(mode="json"),
    )

    with pytest.raises(
        KnowledgeReleaseError,
        match="KNOWLEDGE_RELEASE_VERSION_CONFLICT",
    ):
        KnowledgeReleaseService(repository).build(
            "KP-2026.09.23-RC1",
            created_at=datetime(2026, 9, 23, 11, 0, tzinfo=timezone.utc),
        )


def test_kp_d04_new_release_does_not_mutate_old_release(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    external, _, _ = _seed_release(repository)
    changed = external.model_copy(
        update={
            "object_version": 2,
            "content": "Updated Percentage Used guidance.",
        }
    )
    repository.save(
        f"knowledge/production/published/{changed.object_id}.json",
        changed.model_dump(mode="json"),
    )
    KnowledgeReleaseService(repository).build(
        "KP-2026.09.23-RC2",
        created_at=datetime(2026, 9, 23, 11, 0, tzinfo=timezone.utc),
    )

    service = KnowledgeQueryService(repository)
    old = service.query(
        {
            "knowledge_release_version": "KP-2026.09.23-RC1",
            "object_ids": ["KO-NVME-001"],
        }
    )
    new = service.query(
        {
            "knowledge_release_version": "KP-2026.09.23-RC2",
            "object_ids": ["KO-NVME-001"],
        }
    )

    assert old.objects[0].object_version == 1
    assert new.objects[0].object_version == 2
    assert old.objects[0].content != new.objects[0].content


def test_kp_d04_non_active_objects_are_not_released(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    external = _seed_external(repository)
    stale = external.model_copy(
        update={"object_id": "KO-STALE", "status": "STALE"}
    )
    repository.save(
        "knowledge/production/published/KO-STALE.json",
        stale.model_dump(mode="json"),
    )

    manifest = KnowledgeReleaseService(repository).build(
        "KP-STALE-CHECK",
        created_at=RELEASED_AT,
    )
    result = KnowledgeQueryService(repository).query(
        {"knowledge_release_version": "KP-STALE-CHECK"}
    )

    assert manifest.object_count == 1
    assert [obj.object_id for obj in result.objects] == ["KO-NVME-001"]


def test_kp_d04_missing_evidence_blocks_release(tmp_path: Path) -> None:
    repository = JsonArtifactRepository(tmp_path)
    obj = _seed_external(repository)
    repository.resolve(
        "knowledge/production/evidence/EVD-NVME-001.json"
    ).unlink()

    with pytest.raises(KnowledgeReleaseError, match="EVIDENCE_MISSING"):
        KnowledgeReleaseService(repository).build(
            "KP-MISSING-EVIDENCE",
            created_at=RELEASED_AT,
        )


def test_kp_d04_missing_external_source_blocks_release(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    _seed_external(repository)
    repository.resolve(
        "knowledge/source_documents/NVME/2.0d/source_document.json"
    ).unlink()

    with pytest.raises(KnowledgeReleaseError, match="SOURCE_UNAVAILABLE"):
        KnowledgeReleaseService(repository).build(
            "KP-MISSING-SOURCE",
            created_at=RELEASED_AT,
        )


def test_kp_d04_manifest_hashes_match_release_files(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    _, _, manifest = _seed_release(repository)
    base = repository.resolve(
        "knowledge/production/releases/KP-2026.09.23-RC1"
    )

    for item in manifest.files:
        payload = (base / item["path"]).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == item["sha256"]
        assert len(payload) == item["size"]


def test_kp_d04_package_contains_no_raw_chunk_or_internal_path_contract(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    _seed_release(repository)
    package = repository.resolve(
        "knowledge/production/release_packages/"
        "knowledge-release-KP-2026.09.23-RC1.zip"
    )

    with zipfile.ZipFile(package) as archive:
        text = "\n".join(
            archive.read(name).decode("utf-8", errors="ignore")
            for name in archive.namelist()
        ).lower()

    assert "retrieval_docs" not in text
    assert "raw_chunk" not in text
    assert "top-n chunk" not in text
    assert "internal_path" not in text
    assert str(tmp_path).lower() not in text


def test_kp_d04_release_module_has_no_storage_product_dependency() -> None:
    source = inspect.getsource(release_module)
    assert "storage_life" not in source
    assert "storage_product" not in source


def test_kp_d04_missing_release_is_explicit(tmp_path: Path) -> None:
    with pytest.raises(
        KnowledgeQueryError,
        match="KNOWLEDGE_RELEASE_NOT_FOUND",
    ):
        KnowledgeQueryService(
            JsonArtifactRepository(tmp_path)
        ).query({"knowledge_release_version": "MISSING"})
