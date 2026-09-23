from __future__ import annotations

import hashlib
import json
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator
from urllib.request import Request, urlopen

import pytest

from knowledge_production import (
    KnowledgeExtractionError,
    KnowledgeExtractionOutput,
    KnowledgeExtractionService,
    SourceDocument,
    StructuredDocument,
)
from repositories import JsonArtifactRepository
from runtime import (
    AgentConfigLoader,
    ConfiguredAgentRuntime,
    RuntimeStatus,
    SqliteTaskStore,
)
from tools.openai_mock.server import create_server


ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def running_server() -> Iterator[tuple[str, int]]:
    server = create_server("127.0.0.1", 0)
    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.01},
        daemon=True,
    )
    thread.start()
    try:
        yield server.server_address
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def configure(host: str, port: int, payload: dict) -> None:
    raw = json.dumps(
        {
            "scenario_key": "default",
            "payload": payload,
            "behavior": {},
        },
        ensure_ascii=False,
    ).encode()
    request = Request(
        f"http://{host}:{port}/__mock__/scenario",
        data=raw,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=2) as response:
        assert response.status == 200


def _seed_source(
    repository: JsonArtifactRepository,
) -> tuple[SourceDocument, StructuredDocument]:
    page_1 = (
        "DEVICE HEALTH\n"
        "DEVICE_LIFE_TIME_EST_TYP_A estimates device lifetime for type A.\n"
        "DEVICE_LIFE_TIME_EST_TYP_B estimates device lifetime for type B."
    )
    page_2 = (
        "DEVICE HEALTH\n"
        "PRE_EOL_INFO reports the device pre end-of-life status.\n"
        "Use these fields to diagnose remaining device life."
    )
    document_hash = hashlib.sha256(b"official-emmc-health-pdf").hexdigest()
    source = SourceDocument(
        source_id="EMMC-HEALTH",
        source_version="R1",
        publisher="JEDEC",
        title="eMMC Device Health",
        revision="R1",
        document_type="PDF",
        official_url="https://example.invalid/emmc-health.pdf",
        local_cache_ref=(
            "knowledge/source_documents/EMMC-HEALTH/R1/original.pdf"
        ),
        original_file_name="emmc-health.pdf",
        content_hash=document_hash,
        language="en",
        retrieval_status="PARSED",
    )
    structured = StructuredDocument(
        source_id="EMMC-HEALTH",
        source_version="R1",
        parse_status="PARSED",
        page_count=2,
        blocks=[
            {
                "source_id": "EMMC-HEALTH",
                "source_version": "R1",
                "page": 1,
                "section": "DEVICE HEALTH",
                "source_text": page_1,
                "source_anchor": "page:1",
                "content_hash": hashlib.sha256(
                    page_1.encode("utf-8")
                ).hexdigest(),
            },
            {
                "source_id": "EMMC-HEALTH",
                "source_version": "R1",
                "page": 2,
                "section": "DEVICE HEALTH",
                "source_text": page_2,
                "source_anchor": "page:2",
                "content_hash": hashlib.sha256(
                    page_2.encode("utf-8")
                ).hexdigest(),
            },
        ],
    )
    repository.save(
        "knowledge/source_documents/EMMC-HEALTH/R1/source_document.json",
        source.model_dump(mode="json"),
    )
    repository.save(
        "knowledge/source_documents/EMMC-HEALTH/R1/structured_document.json",
        structured.model_dump(mode="json"),
    )
    original = repository.resolve(source.local_cache_ref)
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(b"official-emmc-health-pdf")
    return source, structured


def _runtime_payload() -> dict:
    common = {
        "device_type": "eMMC",
        "scope": ["device_health"],
        "conditions": [],
        "limitations": [],
        "tags": ["device_health"],
        "confidence": 0.95,
    }
    return {
        "candidates": [
            {
                **common,
                "object_type": "FACT",
                "title": "DEVICE_LIFE_TIME_EST_TYP_A",
                "content": (
                    "DEVICE_LIFE_TIME_EST_TYP_A estimates device lifetime "
                    "for type A."
                ),
                "evidence_locations": [
                    {
                        "source_id": "EMMC-HEALTH",
                        "source_version": "R1",
                        "page": 1,
                        "section": "DEVICE HEALTH",
                        "source_anchor": "page:1",
                    }
                ],
            },
            {
                **common,
                "object_type": "CONCEPT",
                "title": "eMMC device life estimation",
                "content": (
                    "Device health exposes standardized lifetime estimation "
                    "indicators."
                ),
                "evidence_locations": [
                    {
                        "source_id": "EMMC-HEALTH",
                        "source_version": "R1",
                        "page": 1,
                        "section": "DEVICE HEALTH",
                        "source_anchor": "page:1",
                    }
                ],
            },
            {
                **common,
                "object_type": "SOLUTION",
                "title": "Use device health for lifetime monitoring",
                "content": (
                    "Use the device health fields to monitor remaining "
                    "device life."
                ),
                "evidence_locations": [
                    {
                        "source_id": "EMMC-HEALTH",
                        "source_version": "R1",
                        "page": 2,
                        "section": "DEVICE HEALTH",
                        "source_anchor": "page:2",
                    }
                ],
            },
            {
                **common,
                "object_type": "DIAGNOSTIC",
                "title": "PRE_EOL_INFO",
                "content": (
                    "PRE_EOL_INFO reports the device pre end-of-life status."
                ),
                "evidence_locations": [
                    {
                        "source_id": "EMMC-HEALTH",
                        "source_version": "R1",
                        "page": 2,
                        "section": "DEVICE HEALTH",
                        "source_anchor": "page:2",
                    }
                ],
            },
        ],
        "unknowns_or_gaps": [],
    }


def test_kp_m03_real_runtime_mock_extracts_four_candidate_types(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path / "repo")
    source, structured = _seed_source(repository)

    with running_server() as (host, port):
        configure(host, port, _runtime_payload())
        loader = AgentConfigLoader(
            root=ROOT,
            model_profiles={
                "active_model": "qwen_prod",
                "models": {
                    "qwen_prod": {
                        "provider": "openai_compatible",
                        "base_url": f"http://{host}:{port}/v1",
                        "api_key": "KP_M03_TEST_SECRET",
                        "model": "qwen3.8-max",
                        "temperature": 0,
                        "max_tokens": 8192,
                    }
                },
            },
            schemas={
                "KnowledgeExtractionOutput": KnowledgeExtractionOutput,
            },
            environ={},
        )
        runtime = ConfiguredAgentRuntime(
            SqliteTaskStore(tmp_path / "runtime.db"),
            config_loader=loader,
        )
        runtime.load_agent(
            ROOT
            / "config/runtime/agents/knowledge.production.extract.yaml"
        )
        service = KnowledgeExtractionService(repository, runtime)

        candidates = service.extract(source, structured)

    assert {item.object_type for item in candidates} == {
        "FACT",
        "CONCEPT",
        "SOLUTION",
        "DIAGNOSTIC",
    }
    assert all(item.status == "CANDIDATE" for item in candidates)
    assert all(item.evidence_refs for item in candidates)
    evidence = repository.load(
        f"knowledge/production/evidence/{candidates[0].evidence_refs[0]}.json",
        required=True,
    )
    assert evidence is not None
    assert "DEVICE_LIFE_TIME_EST_TYP_A" in evidence["excerpt"]
    assert evidence["source"]["source_id"] == "EMMC-HEALTH"
    assert evidence["locator"]["value"]["page"] == 1


class FakeRuntime:
    def __init__(
        self,
        data: dict | None = None,
        *,
        status: RuntimeStatus = RuntimeStatus.COMPLETED,
        raises: Exception | None = None,
    ) -> None:
        self.data = data
        self.status = status
        self.raises = raises

    def invoke(self, request):
        if self.raises is not None:
            raise self.raises
        return SimpleNamespace(
            status=self.status,
            data=self.data,
            task_id="task-kp-m03",
        )


def test_kp_m03_invalid_semantic_output_fails_closed(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    source, structured = _seed_source(repository)
    payload = _runtime_payload()
    payload["candidates"][0]["evidence_locations"][0][
        "source_text"
    ] = "AI text must never be accepted as evidence"
    service = KnowledgeExtractionService(repository, FakeRuntime(payload))

    with pytest.raises(
        KnowledgeExtractionError, match="KNOWLEDGE_CONTRACT_INVALID"
    ):
        service.extract(source, structured)


def test_kp_m03_missing_evidence_fails_closed(tmp_path: Path) -> None:
    repository = JsonArtifactRepository(tmp_path)
    source, structured = _seed_source(repository)
    payload = _runtime_payload()
    payload["candidates"] = [payload["candidates"][0]]
    payload["candidates"][0]["evidence_locations"][0]["page"] = 99
    payload["candidates"][0]["evidence_locations"][0][
        "source_anchor"
    ] = "page:99"
    service = KnowledgeExtractionService(repository, FakeRuntime(payload))

    with pytest.raises(KnowledgeExtractionError, match="EVIDENCE_MISSING"):
        service.extract(source, structured)


def test_kp_m03_unknown_source_version_fails_closed(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    source, structured = _seed_source(repository)
    payload = _runtime_payload()
    payload["candidates"] = [payload["candidates"][0]]
    payload["candidates"][0]["evidence_locations"][0][
        "source_version"
    ] = "UNKNOWN"
    service = KnowledgeExtractionService(repository, FakeRuntime(payload))

    with pytest.raises(
        KnowledgeExtractionError, match="SOURCE_VERSION_UNKNOWN"
    ):
        service.extract(source, structured)


def test_kp_m03_runtime_failure_is_stable(tmp_path: Path) -> None:
    repository = JsonArtifactRepository(tmp_path)
    source, structured = _seed_source(repository)
    service = KnowledgeExtractionService(
        repository,
        FakeRuntime(raises=RuntimeError("provider down")),
    )

    with pytest.raises(
        KnowledgeExtractionError, match="KNOWLEDGE_EXTRACTION_FAILED"
    ):
        service.extract(source, structured)


def test_kp_m03_non_completed_runtime_result_fails_closed(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path)
    source, structured = _seed_source(repository)
    service = KnowledgeExtractionService(
        repository,
        FakeRuntime(
            _runtime_payload(),
            status=RuntimeStatus.FAILED,
        ),
    )

    with pytest.raises(
        KnowledgeExtractionError, match="KNOWLEDGE_EXTRACTION_FAILED"
    ):
        service.extract(source, structured)
