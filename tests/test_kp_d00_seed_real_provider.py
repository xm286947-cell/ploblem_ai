from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from knowledge_production import KnowledgeExtractionService, SourceDocumentService
from repositories import JsonArtifactRepository


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SHA256 = "c5853ab4b389caa5d03f6b41597d49f9fc5abdfa5e4b1c01a021d6e3d89f875e"
TOPICS = [
    "Percentage Used",
    "Available Spare",
    "Data Units Written",
    "Critical Warning",
    "Media and Data Integrity Errors",
]


@pytest.mark.skipif(
    os.environ.get("KNOWLEDGE_PRODUCTION_REAL_E2E", "").lower()
    not in {"1", "true", "yes", "on"},
    reason="real Knowledge Production provider acceptance is opt-in",
)
def test_kp_d00_seed_pdf_real_provider_to_candidate_evidence(
    tmp_path: Path,
) -> None:
    pdf_path = Path(os.environ["KP_SEED_PDF"]).resolve()
    assert pdf_path.is_file(), "KP_SEED_PDF must point to the extracted Seed PDF"
    assert hashlib.sha256(pdf_path.read_bytes()).hexdigest() == EXPECTED_SHA256

    repository = JsonArtifactRepository(tmp_path / "knowledge-repo")
    source, structured = SourceDocumentService(repository).ingest_pdf(
        pdf_path,
        source_id="NVM-EXPRESS-BASE-2.0D",
        publisher="NVM Express",
        title="NVM Express Base Specification",
        version="2.0d",
        revision="2.0d",
        official_url=(
            "https://nvmexpress.org/wp-content/uploads/"
            "NVM-Express-Base-Specification-2.0d-2024.01.11-Ratified.pdf"
        ),
        source_ref=(
            "Storage_External_Knowledge_Materials_V0.1/"
            "03_SSD_NVME/NVM-Express-Base-2.0d.pdf"
        ),
        language="en",
    )

    assert source.retrieval_status == "PARSED"
    assert source.content_hash == EXPECTED_SHA256
    parsed_text = "\n".join(block.source_text for block in structured.blocks)
    for topic in TOPICS:
        assert topic.lower() in parsed_text.lower()

    model_config = (
        os.environ.get("KNOWLEDGE_MODEL_CONFIG")
        or os.environ.get("STORAGE_MODEL_CONFIG")
        or None
    )
    service = KnowledgeExtractionService.from_project(
        ROOT,
        model_config_path=model_config,
        runtime_db_path=tmp_path / "runtime.db",
        environ=os.environ,
    )
    candidates = service.extract(
        source,
        structured,
        requested_topics=TOPICS,
    )

    assert candidates
    assert all(candidate.status == "CANDIDATE" for candidate in candidates)
    assert not repository.list("knowledge/production/published")

    searchable = [
        (
            candidate,
            (candidate.title + "\n" + candidate.content).lower(),
        )
        for candidate in candidates
    ]
    for topic in TOPICS:
        matches = [
            candidate
            for candidate, text in searchable
            if topic.lower() in text
        ]
        assert matches, f"missing candidate for {topic}"

        traceable = False
        for candidate in matches:
            for evidence_id in candidate.evidence_refs:
                evidence = repository.load(
                    f"knowledge/production/evidence/{evidence_id}.json",
                    required=True,
                )
                assert evidence is not None
                assert evidence["source"]["source_id"] == source.source_id
                assert evidence["source"]["revision"] == source.source_version
                assert evidence["source"]["content_hash"] == EXPECTED_SHA256
                if topic.lower() in (evidence.get("excerpt") or "").lower():
                    traceable = True
        assert traceable, f"{topic} lacks rebound Seed evidence"
