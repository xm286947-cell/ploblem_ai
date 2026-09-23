from __future__ import annotations

import os
from pathlib import Path

import pytest

from knowledge_production import (
    KnowledgeExtractionOutput,
    KnowledgeExtractionService,
    SourceDocumentService,
)
from repositories import JsonArtifactRepository
from runtime import AgentConfigLoader, ConfiguredAgentRuntime, SqliteTaskStore


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SHA256 = (
    "a40b2ecc44c89184dbcaed3f8d48e7615e4daefaf7732076a40f7bc73751e978"
)
TOPICS = [
    "DEVICE_LIFE_TIME_EST_TYP_A",
    "DEVICE_LIFE_TIME_EST_TYP_B",
    "PRE_EOL_INFO",
]


def _ingest_real_pdf(tmp_path: Path):
    pdf_path = Path(os.environ["KP_REAL_PDF"]).resolve()
    assert pdf_path.is_file()

    repository = JsonArtifactRepository(tmp_path / "knowledge-repo")
    source_service = SourceDocumentService(repository)
    source, structured = source_service.ingest_pdf(
        pdf_path,
        source_id="skyhigh_s40fc016_002_01118",
        publisher="SkyHigh Memory",
        title="S40FC016 16GB e.MMC Flash",
        revision="D",
        official_url=(
            "https://www.skyhighmemory.com/download/"
            "eMMC_16GB_STD_PKG_S40FC016C3_002_01118.pdf?v=D"
        ),
        language="en",
    )
    return repository, source, structured


def _assert_real_pdf_source(source, structured) -> None:
    assert source.content_hash == EXPECTED_SHA256
    assert source.retrieval_status == "PARSED"
    assert structured.page_count == 33
    parsed_text = "\n".join(block.source_text for block in structured.blocks)
    for topic in TOPICS:
        assert topic in parsed_text


def test_kp_first_golden_real_pdf_source_parse(
    tmp_path: Path,
) -> None:
    repository, source, structured = _ingest_real_pdf(tmp_path)

    _assert_real_pdf_source(source, structured)
    assert repository.resolve(source.local_cache_ref).is_file()


@pytest.mark.skipif(
    os.environ.get("KNOWLEDGE_PRODUCTION_REAL_E2E", "").lower()
    not in {"1", "true", "yes", "on"},
    reason="KNOWLEDGE_PRODUCTION_REAL_E2E opt-in disabled",
)
def test_kp_first_golden_real_pdf_to_candidate_evidence(
    tmp_path: Path,
) -> None:
    repository, source, structured = _ingest_real_pdf(tmp_path)
    _assert_real_pdf_source(source, structured)

    loader = AgentConfigLoader(
        root=ROOT,
        model_profiles=ROOT / "config/runtime/model.yaml",
        schemas={"KnowledgeExtractionOutput": KnowledgeExtractionOutput},
        environ=os.environ,
    )
    runtime = ConfiguredAgentRuntime(
        SqliteTaskStore(tmp_path / "runtime.db"),
        config_loader=loader,
    )
    runtime.load_agent(
        ROOT / "config/runtime/agents/knowledge.production.extract.yaml"
    )
    service = KnowledgeExtractionService(repository, runtime)

    candidates = service.extract(
        source,
        structured,
        requested_topics=TOPICS,
    )

    by_title = {candidate.title: candidate for candidate in candidates}
    for topic in TOPICS:
        assert topic in by_title
        candidate = by_title[topic]
        assert candidate.status == "CANDIDATE"
        assert candidate.evidence_refs
        evidence = repository.load(
            (
                "knowledge/production/evidence/"
                f"{candidate.evidence_refs[0]}.json"
            ),
            required=True,
        )
        assert evidence is not None
        assert evidence["source"]["source_id"] == source.source_id
        assert evidence["source"]["revision"] == source.source_version
        assert evidence["source"]["content_hash"] == source.content_hash
        assert evidence["locator"]["value"]["page"] >= 1
        assert topic in evidence["excerpt"]

    assert not repository.list("knowledge/production/published")
