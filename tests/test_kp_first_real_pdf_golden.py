from __future__ import annotations

import hashlib
import os
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

from knowledge_production import (
    KnowledgeExtractionOutput,
    KnowledgeExtractionService,
    SourceDocumentService,
)
from repositories import JsonArtifactRepository
from runtime import AgentConfigLoader, ConfiguredAgentRuntime, SqliteTaskStore


ROOT = Path(__file__).resolve().parents[1]
PDF_URL = (
    "https://media.digikey.com/pdf/Data%20Sheets/Toshiba%20PDFs/"
    "Kioxia_THGAMRG7T13BAIL_BiCS3%2016GB_5_1_E_rev3_0_20200210.pdf"
)
KIOXIA_PRODUCT_URL = (
    "https://www.kioxia.com/en-jp/business/news/2019/20190227-2.html"
)
EXPECTED_TERMS = (
    "DEVICE_LIFE_TIME_EST_TYP_A",
    "DEVICE_LIFE_TIME_EST_TYP_B",
    "PRE_EOL_INFO",
)


def _download_pdf(target: Path) -> None:
    request = Request(
        PDF_URL,
        headers={
            "User-Agent": "Mozilla/5.0 KnowledgeProductionGolden/1.0",
            "Accept": "application/pdf,*/*",
        },
    )
    with urlopen(request, timeout=60) as response:
        payload = response.read()
    assert payload.startswith(b"%PDF"), "golden source is not a PDF"
    assert len(payload) > 100_000, "golden PDF download is unexpectedly small"
    target.write_bytes(payload)


@pytest.mark.skipif(
    os.environ.get("KNOWLEDGE_PRODUCTION_REAL_GOLDEN", "").lower()
    not in {"1", "true", "yes", "on"},
    reason="real Knowledge Production golden path is opt-in",
)
def test_kp_first_real_pdf_golden_path(tmp_path: Path) -> None:
    pdf = tmp_path / "KIOXIA_THGAMRG7T13BAIL.pdf"
    _download_pdf(pdf)

    repository = JsonArtifactRepository(tmp_path / "knowledge-repo")
    source_service = SourceDocumentService(repository)
    source, structured = source_service.ingest_pdf(
        pdf,
        source_id="KIOXIA-THGAMRG7T13BAIL",
        publisher="KIOXIA Corporation",
        title="e-MMC Module 16GB THGAMRG7T13BAIL",
        revision="2020-02-10",
        official_url=KIOXIA_PRODUCT_URL,
        source_ref=PDF_URL,
        language="en",
    )

    assert source.retrieval_status == "PARSED"
    assert source.content_hash == hashlib.sha256(pdf.read_bytes()).hexdigest()
    assert repository.resolve(source.local_cache_ref).is_file()
    assert structured.page_count >= 31

    health_blocks = [
        block for block in structured.blocks
        if all(term in block.source_text for term in EXPECTED_TERMS)
    ]
    assert health_blocks, "parsed PDF did not preserve the eMMC health fields"
    assert any(block.page == 7 for block in health_blocks)

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

    extraction = KnowledgeExtractionService(repository, runtime)
    candidates = extraction.extract(source, structured)

    assert candidates, "real provider returned no knowledge candidates"
    assert all(candidate.status == "CANDIDATE" for candidate in candidates)

    for term in EXPECTED_TERMS:
        matching = [
            candidate for candidate in candidates
            if term in candidate.title or term in candidate.content
        ]
        assert matching, f"missing expected candidate: {term}"

        traceable = False
        for candidate in matching:
            for evidence_id in candidate.evidence_refs:
                evidence = repository.load(
                    f"knowledge/production/evidence/{evidence_id}.json",
                    required=True,
                )
                assert evidence is not None
                if term in (evidence.get("excerpt") or ""):
                    locator = evidence["locator"]["value"]
                    assert locator["page"] == 7
                    assert evidence["source"]["source_id"] == source.source_id
                    assert evidence["source"]["revision"] == source.source_version
                    traceable = True
        assert traceable, f"{term} has no original-PDF evidence"

    published = repository.list("knowledge/production/published")
    assert published == [], "KP-M03 must never auto-publish candidates"
