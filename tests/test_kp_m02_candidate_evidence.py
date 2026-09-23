from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from knowledge_production import (
    EvidenceLocation,
    KnowledgeCandidate,
    KnowledgeCandidateError,
    KnowledgeCandidateService,
    source_ref_key,
)
from repositories import JsonArtifactRepository
from runtime.contracts import EvidenceReference


def _seed_source(repository: JsonArtifactRepository) -> None:
    page_1 = "DEVICE_LIFE_TIME_EST_TYP_A reports estimated device lifetime."
    page_2 = "PRE_EOL_INFO reports device pre end-of-life status."
    document_hash = hashlib.sha256(b"official-pdf").hexdigest()
    repository.save(
        "knowledge/source_documents/SRC/R1/source_document.json",
        {
            "source_id": "SRC",
            "source_version": "R1",
            "publisher": "JEDEC",
            "title": "eMMC Device Health",
            "document_type": "PDF",
            "official_url": "https://example.invalid/emmc.pdf",
            "content_hash": document_hash,
        },
    )
    original = repository.resolve(
        "knowledge/source_documents/SRC/R1/original.pdf"
    )
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(b"official-pdf")
    repository.save(
        "knowledge/source_documents/SRC/R1/structured_document.json",
        {
            "blocks": [
                {
                    "page": 1,
                    "section": "DEVICE HEALTH",
                    "source_anchor": "page:1",
                    "source_text": page_1,
                    "content_hash": hashlib.sha256(
                        page_1.encode("utf-8")
                    ).hexdigest(),
                },
                {
                    "page": 2,
                    "section": "DEVICE HEALTH",
                    "source_anchor": "page:2",
                    "source_text": page_2,
                    "content_hash": hashlib.sha256(
                        page_2.encode("utf-8")
                    ).hexdigest(),
                },
            ]
        },
    )


def _candidate(
    evidence_refs: list[str],
    *,
    candidate_id: str = "CAND-1",
    object_type: str = "DIAGNOSTIC",
) -> KnowledgeCandidate:
    return KnowledgeCandidate(
        candidate_id=candidate_id,
        object_type=object_type,
        title="eMMC device health diagnostics",
        summary="Interpret standardized eMMC device-health indicators.",
        content="Use device-health indicators as diagnostic knowledge.",
        device_type="eMMC",
        scope=["device_health"],
        conditions=[],
        limitations=[],
        tags=["health"],
        evidence_refs=evidence_refs,
        source_refs=[source_ref_key("SRC", "R1")],
        extraction_version="kp-m02-test",
        confidence=0.9,
    )


def _bind(
    service: KnowledgeCandidateService, page: int
) -> EvidenceReference:
    return service.bind_evidence(
        EvidenceLocation(
            source_id="SRC",
            source_version="R1",
            page=page,
            section="DEVICE HEALTH",
            source_anchor=f"page:{page}",
        )
    )


def test_kp_m02_candidate_model_supports_required_object_types(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    _seed_source(repository)
    service = KnowledgeCandidateService(repository)
    evidence = _bind(service, 1)

    for index, object_type in enumerate(
        ["FACT", "CONCEPT", "SOLUTION", "DIAGNOSTIC", "REQUIREMENT"]
    ):
        candidate = _candidate(
            [evidence.evidence_id],
            candidate_id=f"CAND-{index}",
            object_type=object_type,
        )
        assert service.save_candidate(candidate).object_type == object_type


def test_kp_m02_reuses_runtime_evidence_contract_and_source_traceability(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    _seed_source(repository)
    service = KnowledgeCandidateService(repository)

    evidence = _bind(service, 1)

    assert isinstance(evidence, EvidenceReference)
    assert evidence.source.source_id == "SRC"
    assert evidence.source.revision == "R1"
    assert evidence.locator.value["page"] == 1
    assert evidence.locator.value["section"] == "DEVICE HEALTH"
    assert "DEVICE_LIFE_TIME_EST_TYP_A" in (evidence.excerpt or "")
    assert repository.resolve(
        "knowledge/source_documents/SRC/R1/original.pdf"
    ).is_file()


def test_kp_m02_multiple_evidence_supported(tmp_path: Path) -> None:
    repository = JsonArtifactRepository(tmp_path)
    _seed_source(repository)
    service = KnowledgeCandidateService(repository)

    evidence_1 = _bind(service, 1)
    evidence_2 = _bind(service, 2)
    candidate = service.save_candidate(
        _candidate([evidence_1.evidence_id, evidence_2.evidence_id])
    )

    assert candidate.evidence_refs == [
        evidence_1.evidence_id,
        evidence_2.evidence_id,
    ]


def test_kp_m02_missing_evidence_blocks_candidate(tmp_path: Path) -> None:
    repository = JsonArtifactRepository(tmp_path)
    _seed_source(repository)
    service = KnowledgeCandidateService(repository)

    with pytest.raises(KnowledgeCandidateError, match="EVIDENCE_MISSING"):
        service.save_candidate(_candidate(["EVD-MISSING"]))


def test_kp_m02_ai_text_cannot_be_supplied_as_evidence() -> None:
    with pytest.raises(ValidationError):
        EvidenceLocation(
            source_id="SRC",
            source_version="R1",
            page=1,
            source_anchor="page:1",
            source_text="AI-generated text must never become evidence",
        )


def test_kp_m02_evidence_is_rebound_from_structured_document(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    _seed_source(repository)
    service = KnowledgeCandidateService(repository)

    evidence = _bind(service, 2)

    assert evidence.excerpt == (
        "PRE_EOL_INFO reports device pre end-of-life status."
    )
    assert evidence.metadata["evidence_status"] == "BOUND"


def test_kp_m02_source_reference_must_match_bound_evidence(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    _seed_source(repository)
    service = KnowledgeCandidateService(repository)
    evidence = _bind(service, 1)
    candidate = _candidate([evidence.evidence_id]).model_copy(
        update={"source_refs": ["OTHER@R1"]}
    )

    with pytest.raises(
        KnowledgeCandidateError, match="SOURCE_TRACEABILITY_INVALID"
    ):
        service.save_candidate(candidate)
