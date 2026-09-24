from __future__ import annotations

import hashlib
import json
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from urllib.request import Request, urlopen

import pytest
from pydantic import ValidationError

from knowledge_production import (
    KnowledgeEvaluationService,
    KnowledgeExtractionError,
    KnowledgeExtractionOutput,
    KnowledgeExtractionService,
    KnowledgePublishService,
    KnowledgeQueryService,
    KnowledgeReleaseService,
    KnowledgeReviewService,
    SourceDocument,
    StructuredDocument,
)
from repositories import JsonArtifactRepository
from runtime import AgentConfigLoader, ConfiguredAgentRuntime, SqliteTaskStore
from tools.openai_mock.server import create_server


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = ROOT / "product_test" / "case_knowledge" / "fixtures" / "openai"
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


@contextmanager
def running_openai_mock() -> Iterator[tuple[str, int]]:
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


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


def configure_fixture(host: str, port: int, name: str) -> dict:
    fixture = load_fixture(name)
    raw = json.dumps(fixture, ensure_ascii=False).encode("utf-8")
    request = Request(
        f"http://{host}:{port}/__mock__/scenario",
        data=raw,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=2) as response:
        assert response.status == 200
    return fixture


def mock_counters(host: str, port: int) -> dict[str, int]:
    with urlopen(f"http://{host}:{port}/__mock__/counters", timeout=2) as response:
        return json.loads(response.read().decode("utf-8"))["data"]


def mock_requests(host: str, port: int) -> list[dict]:
    with urlopen(f"http://{host}:{port}/__mock__/requests", timeout=2) as response:
        return json.loads(response.read().decode("utf-8"))["data"]


def build_runtime(host: str, port: int, tmp_path: Path) -> ConfiguredAgentRuntime:
    loader = AgentConfigLoader(
        root=ROOT,
        model_profiles={
            "active_model": "qwen_prod",
            "models": {
                "qwen_prod": {
                    "provider": "openai_compatible",
                    "base_url": f"http://{host}:{port}/v1",
                    "api_key": "CASE_KNOWLEDGE_TEST_SECRET",
                    "model": "mock-gpt",
                    "temperature": 0,
                    "max_tokens": 8192,
                }
            },
        },
        schemas={"KnowledgeExtractionOutput": KnowledgeExtractionOutput},
        environ={},
    )
    runtime = ConfiguredAgentRuntime(
        SqliteTaskStore(tmp_path / "runtime.sqlite3"),
        config_loader=loader,
    )
    runtime.load_agent(
        ROOT / "config" / "runtime" / "agents" / "knowledge.production.extract.yaml"
    )
    return runtime


def seed_source(
    repository: JsonArtifactRepository,
    *,
    source_id: str,
    source_version: str,
    title: str,
    publisher: str,
    pages: list[tuple[int, str, str]],
) -> tuple[SourceDocument, StructuredDocument]:
    source_bytes = f"{source_id}:{source_version}:controlled-test-source".encode("utf-8")
    content_hash = hashlib.sha256(source_bytes).hexdigest()
    source = SourceDocument(
        source_id=source_id,
        source_version=source_version,
        publisher=publisher,
        title=title,
        version=source_version,
        revision=source_version,
        document_type="PDF",
        official_url="https://example.invalid/controlled-test-source.pdf",
        source_ref=f"synthetic/{source_id}-{source_version}.pdf",
        local_cache_ref=(
            f"knowledge/source_documents/{source_id}/{source_version}/original.pdf"
        ),
        original_file_name=f"{source_id}.pdf",
        content_hash=content_hash,
        language="en",
        retrieval_status="PARSED",
        created_at=NOW,
    )
    blocks = []
    for page, section, text in pages:
        blocks.append(
            {
                "source_id": source_id,
                "source_version": source_version,
                "page": page,
                "section": section,
                "source_text": text,
                "source_anchor": f"page:{page}",
                "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }
        )
    structured = StructuredDocument(
        source_id=source_id,
        source_version=source_version,
        parse_status="PARSED",
        page_count=len(pages),
        blocks=blocks,
    )
    repository.save(
        f"knowledge/source_documents/{source_id}/{source_version}/source_document.json",
        source.model_dump(mode="json"),
    )
    repository.save(
        f"knowledge/source_documents/{source_id}/{source_version}/structured_document.json",
        structured.model_dump(mode="json"),
    )
    original = repository.resolve(source.local_cache_ref)
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(source_bytes)
    return source, structured


def nvme_source(repository: JsonArtifactRepository):
    return seed_source(
        repository,
        source_id="NVME-HEALTH",
        source_version="2.0d",
        title="NVM Express Base Specification - controlled regression projection",
        publisher="NVM Express",
        pages=[
            (100, "SMART / Health", "Percentage Used contains an estimate of NVM subsystem life consumed."),
            (101, "SMART / Health", "Available Spare reports the normalized percentage of remaining spare capacity."),
            (102, "SMART / Health", "Data Units Written reports the amount of data written by the host."),
            (103, "SMART / Health", "Critical Warning indicates critical NVM subsystem conditions requiring attention."),
            (104, "SMART / Health", "Media and Data Integrity Errors reports unrecovered data integrity errors detected by the controller."),
        ],
    )


def ubi_source(repository: JsonArtifactRepository):
    return seed_source(
        repository,
        source_id="LINUX-UBI",
        source_version="V1",
        title="Linux UBI - controlled regression projection",
        publisher="Linux kernel documentation",
        pages=[
            (10, "UBI terminology", "A PEB is a physical eraseblock managed by the UBI layer."),
            (11, "UBI terminology", "A LEB is a logical eraseblock mapped by UBI onto a physical eraseblock."),
            (12, "Wear leveling", "UBI performs wear leveling by managing eraseblock mappings and erase counters."),
            (13, "Erase counters", "Erase counters can be used to observe erase history across physical eraseblocks."),
        ],
    )


def extract_with_fixture(
    tmp_path: Path,
    fixture_name: str,
    source_factory,
):
    repository = JsonArtifactRepository(tmp_path / "repo")
    source, structured = source_factory(repository)
    with running_openai_mock() as (host, port):
        configure_fixture(host, port, fixture_name)
        service = KnowledgeExtractionService(
            repository,
            build_runtime(host, port, tmp_path),
        )
        result = service.extract(source, structured)
        requests = mock_requests(host, port)
    return repository, result, requests


def assert_no_published(repository: JsonArtifactRepository) -> None:
    assert repository.list("knowledge/production/published") == []


def publish_all(repository: JsonArtifactRepository, candidates):
    objects = []
    for candidate in candidates:
        evaluation = KnowledgeEvaluationService(repository).evaluate(candidate)
        assert evaluation.review_ready is True
        assert evaluation.publish_readiness is True
        KnowledgeReviewService(repository).confirm(
            candidate.candidate_id,
            evaluation.evaluation_id,
            reviewed_by="case-knowledge-test-reviewer",
            reviewed_at=NOW,
            review_note="Frozen product test baseline",
        )
        objects.append(
            KnowledgePublishService(repository).publish(
                candidate.candidate_id,
                published_by="case-knowledge-test-publisher",
                published_at=NOW,
            )
        )
    return objects


def test_ck_sys_external_nvme_mock_to_release_query(tmp_path: Path) -> None:
    repository, candidates, requests = extract_with_fixture(
        tmp_path,
        "nvme_health_golden.json",
        nvme_source,
    )

    assert len(candidates) == 5
    assert {item.title for item in candidates} == {
        "Percentage Used",
        "Available Spare",
        "Data Units Written",
        "Critical Warning",
        "Media Errors",
    }
    assert all(item.status.value == "CANDIDATE" for item in candidates)
    assert all(item.evidence_refs for item in candidates)
    assert_no_published(repository)

    assert requests
    assert all(
        row.get("path") in {"/v1/chat/completions", "/v1/responses"}
        for row in requests
    )
    assert all(
        row.get("headers", {}).get("Authorization") in {None, "[REDACTED]"}
        for row in requests
    )

    objects = publish_all(repository, candidates)
    assert len(objects) == 5
    assert all(item.status.value == "ACTIVE" for item in objects)

    manifest = KnowledgeReleaseService(repository).build(
        "CASE-KNOWLEDGE-MOCK-R1",
        created_at=NOW,
    )
    assert manifest.object_count == 5

    result = KnowledgeQueryService(repository).query(
        {
            "knowledge_release_version": "CASE-KNOWLEDGE-MOCK-R1",
            "device_types": ["SSD"],
        }
    )
    assert len(result.objects) == 5
    assert len(result.evidences) == 5
    assert len(result.source_references) == 1
    assert result.unknowns_or_gaps == []
    assert all(
        item.knowledge_release_version == "CASE-KNOWLEDGE-MOCK-R1"
        for item in result.objects
    )


def test_ck_sys_external_ubi_mock_candidate_and_evidence(tmp_path: Path) -> None:
    repository, candidates, _ = extract_with_fixture(
        tmp_path,
        "linux_ubi_golden.json",
        ubi_source,
    )

    assert len(candidates) == 4
    assert {item.object_type.value for item in candidates} == {
        "CONCEPT",
        "SOLUTION",
        "DIAGNOSTIC",
    }
    assert all(item.status.value == "CANDIDATE" for item in candidates)
    assert all(item.evidence_refs for item in candidates)
    assert_no_published(repository)

    objects = publish_all(repository, candidates)
    KnowledgeReleaseService(repository).build(
        "CASE-KNOWLEDGE-UBI-R1",
        created_at=NOW,
    )
    result = KnowledgeQueryService(repository).query(
        {
            "knowledge_release_version": "CASE-KNOWLEDGE-UBI-R1",
            "device_types": ["RAW_NAND"],
        }
    )
    assert {item.object_id for item in result.objects} == {
        item.object_id for item in objects
    }
    assert result.evidences


def test_ck_fail_missing_evidence_is_fail_closed(tmp_path: Path) -> None:
    repository = JsonArtifactRepository(tmp_path / "repo")
    source, structured = nvme_source(repository)

    with running_openai_mock() as (host, port):
        configure_fixture(host, port, "missing_evidence.json")
        service = KnowledgeExtractionService(
            repository,
            build_runtime(host, port, tmp_path),
        )
        with pytest.raises(KnowledgeExtractionError) as exc:
            service.extract(source, structured)

    assert exc.value.code == "EVIDENCE_MISSING"
    assert repository.list("knowledge/production/candidates") == []
    assert_no_published(repository)


def test_ck_fail_ai_cannot_set_business_publish_status(tmp_path: Path) -> None:
    fixture = load_fixture("ai_privilege_escalation.json")
    with pytest.raises(ValidationError):
        KnowledgeExtractionOutput.model_validate(fixture["payload"])

    repository = JsonArtifactRepository(tmp_path / "repo")
    source, structured = nvme_source(repository)
    with running_openai_mock() as (host, port):
        configure_fixture(host, port, "ai_privilege_escalation.json")
        service = KnowledgeExtractionService(
            repository,
            build_runtime(host, port, tmp_path),
        )
        with pytest.raises(KnowledgeExtractionError):
            service.extract(source, structured)

    assert repository.list("knowledge/production/candidates") == []
    assert_no_published(repository)


@pytest.mark.parametrize(
    "fixture_name",
    ["provider_429.json", "provider_invalid_json.json"],
)
def test_ck_fail_provider_errors_never_create_knowledge(
    tmp_path: Path,
    fixture_name: str,
) -> None:
    repository = JsonArtifactRepository(tmp_path / "repo")
    source, structured = nvme_source(repository)

    with running_openai_mock() as (host, port):
        configure_fixture(host, port, fixture_name)
        service = KnowledgeExtractionService(
            repository,
            build_runtime(host, port, tmp_path),
        )
        with pytest.raises(KnowledgeExtractionError) as exc:
            service.extract(source, structured)
        counters = mock_counters(host, port)

    assert exc.value.code == "KNOWLEDGE_EXTRACTION_FAILED"
    assert 1 <= counters.get("default", 0) <= 2
    assert repository.list("knowledge/production/candidates") == []
    assert_no_published(repository)
