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

# #475 regression: an explicit rerun is a NEW Runtime task only after a
# verified FAILED predecessor. No real Provider or Formal Release is used.
from knowledge_production import ExtractionRerunAuthorization
from runtime import LightweightExecutionEngine, RuntimeStepError


def _logical_request_id(source: SourceDocument) -> str:
    empty_topics_hash = hashlib.sha256(b"[]").hexdigest()[:12]
    return (
        f"knowledge-extract:{source.source_id}:"
        f"{source.source_version}:"
        f"{source.content_hash[:12]}:{empty_topics_hash}"
    )


def _test_engine(tmp_path: Path, handler):
    store = SqliteTaskStore(tmp_path / "rerun-runtime.sqlite3")
    runtime = LightweightExecutionEngine(store)
    runtime.register_agent("knowledge.production.extract", handler)
    return runtime, store


def test_kp_m03_failed_extraction_requires_explicit_rerun(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path / "repo")
    source, structured = _seed_source(repository)
    calls = []

    def handler(payload, context):
        calls.append("attempt")
        raise RuntimeStepError(
            "controlled rejected request",
            code="PROVIDER_HTTP_ERROR",
            retryable=False,
        )

    runtime, store = _test_engine(tmp_path, handler)
    service = KnowledgeExtractionService(repository, runtime)
    request_id = _logical_request_id(source)

    with pytest.raises(KnowledgeExtractionError, match="KNOWLEDGE_EXTRACTION_FAILED"):
        service.extract(source, structured)
    previous = store.get_task_by_request_id(request_id)
    assert previous is not None
    assert previous.status == RuntimeStatus.FAILED
    assert calls == ["attempt"]

    # A normal retry must return the old FAILED Task, without a new call.
    with pytest.raises(KnowledgeExtractionError, match="KNOWLEDGE_EXTRACTION_FAILED"):
        service.extract(source, structured)
    assert store.get_task_by_request_id(request_id).task_id == previous.task_id
    assert calls == ["attempt"]
    assert repository.list("knowledge/production/candidates") == []


def test_kp_m03_explicit_rerun_creates_new_task_and_audit_lineage(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path / "repo")
    source, structured = _seed_source(repository)
    attempts = []

    def handler(payload, context):
        attempts.append("attempt")
        if len(attempts) == 1:
            raise RuntimeStepError(
                "old provider authorization failure",
                code="PROVIDER_HTTP_ERROR",
                retryable=False,
            )
        return _runtime_payload()

    runtime, store = _test_engine(tmp_path, handler)
    service = KnowledgeExtractionService(repository, runtime)
    base_id = _logical_request_id(source)

    with pytest.raises(KnowledgeExtractionError):
        service.extract(source, structured)
    previous = store.get_task_by_request_id(base_id)
    assert previous is not None and previous.status == RuntimeStatus.FAILED

    authorization = ExtractionRerunAuthorization(
        generation=1,
        failed_task_id=previous.task_id,
        approved_by="kp-test-operator",
        reason="approved provider configuration recovery",
        approval_ref="KP-475-RERUN-APPROVAL-001",
    )
    candidates = service.extract(
        source, structured, rerun_authorization=authorization
    )
    assert len(candidates) == 4
    rerun_id = f"{base_id}:rerun:1"
    new_task = store.get_task_by_request_id(rerun_id)
    assert new_task is not None
    assert new_task.status == RuntimeStatus.COMPLETED
    assert new_task.task_id != previous.task_id
    assert new_task.input_hash == previous.input_hash
    assert store.get_task_by_request_id(base_id).task_id == previous.task_id
    lineage = new_task.metadata["extraction_rerun"]
    assert lineage["rerun_of_task_id"] == previous.task_id
    assert lineage["rerun_of_request_id"] == base_id
    assert lineage["approval_ref"] == "KP-475-RERUN-APPROVAL-001"
    assert lineage["business_input_hash"] == previous.input_hash

    # Repeating the SAME approved generation replays committed Runtime result,
    # and saves no additional Knowledge Candidates or Evidence.
    again = service.extract(
        source, structured, rerun_authorization=authorization
    )
    assert [x.candidate_id for x in again] == [x.candidate_id for x in candidates]
    assert attempts == ["attempt", "attempt"]
    assert len(repository.list("knowledge/production/candidates")) == 4
    assert len(repository.list("knowledge/production/evidence")) > 0
    assert repository.list("knowledge/production/published") == []
    assert repository.list("knowledge/production/releases") == []


def test_kp_m03_rerun_rejects_unverified_failed_task(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path / "repo")
    source, structured = _seed_source(repository)
    attempts = []

    def handler(payload, context):
        attempts.append("attempt")
        raise RuntimeStepError("forced failure", retryable=False)

    runtime, store = _test_engine(tmp_path, handler)
    service = KnowledgeExtractionService(repository, runtime)
    with pytest.raises(KnowledgeExtractionError):
        service.extract(source, structured)
    old = store.get_task_by_request_id(_logical_request_id(source))
    assert old is not None
    good = dict(
        generation=1,
        failed_task_id=old.task_id,
        approved_by="kp-test-operator",
        reason="controlled retry",
        approval_ref="APPROVAL-001",
    )
    for change in (
        {"generation": 0},
        {"generation": 2},
        {"failed_task_id": "task-does-not-exist"},
        {"approved_by": ""},
        {"reason": ""},
        {"approval_ref": ""},
    ):
        with pytest.raises(
            KnowledgeExtractionError,
            match="EXTRACTION_RERUN_NOT_AUTHORIZED",
        ):
            service.extract(
                source,
                structured,
                rerun_authorization=ExtractionRerunAuthorization(
                    **{**good, **change}
                ),
            )
    assert attempts == ["attempt"]
    assert store.get_task_by_request_id(
        _logical_request_id(source) + ":rerun:1"
    ) is None


def test_kp_m03_rerun_rejects_changed_business_input_and_success_parent(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path / "repo")
    source, structured = _seed_source(repository)
    attempts = []

    def handler(payload, context):
        attempts.append("attempt")
        raise RuntimeStepError("first fails", retryable=False)

    runtime, store = _test_engine(tmp_path, handler)
    service = KnowledgeExtractionService(repository, runtime)
    with pytest.raises(KnowledgeExtractionError):
        service.extract(source, structured)
    old = store.get_task_by_request_id(_logical_request_id(source))
    assert old is not None

    auth = ExtractionRerunAuthorization(
        generation=1,
        failed_task_id=old.task_id,
        approved_by="kp-test-operator",
        reason="provider recovered",
        approval_ref="APPROVAL-002",
    )
    altered = structured.model_copy(deep=True)
    altered.blocks[0].source_text += " silently edited"
    with pytest.raises(
        KnowledgeExtractionError, match="EXTRACTION_RERUN_NOT_AUTHORIZED"
    ):
        service.extract(source, altered, rerun_authorization=auth)
    assert attempts == ["attempt"]

    # A completed extraction must never be used as a FAILED predecessor.
    other_repo = JsonArtifactRepository(tmp_path / "successful-repo")
    other_source, other_structured = _seed_source(other_repo)
    complete_calls = []
    complete_runtime, complete_store = _test_engine(
        tmp_path / "successful-runtime",
        lambda payload, context: (
            complete_calls.append("attempt") or _runtime_payload()
        ),
    )
    complete_service = KnowledgeExtractionService(other_repo, complete_runtime)
    complete_service.extract(other_source, other_structured)
    completed = complete_store.get_task_by_request_id(
        _logical_request_id(other_source)
    )
    assert completed is not None and completed.status == RuntimeStatus.COMPLETED
    with pytest.raises(
        KnowledgeExtractionError, match="EXTRACTION_RERUN_NOT_AUTHORIZED"
    ):
        complete_service.extract(
            other_source,
            other_structured,
            rerun_authorization=ExtractionRerunAuthorization(
                generation=1,
                failed_task_id=completed.task_id,
                approved_by="kp-test-operator",
                reason="attempt to rerun success",
                approval_ref="APPROVAL-003",
            ),
        )
    assert complete_calls == ["attempt"]


def test_kp_m03_rerun_generation_two_requires_failed_generation_one(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path / "repo")
    source, structured = _seed_source(repository)
    calls = []

    def handler(payload, context):
        calls.append("attempt")
        if len(calls) <= 2:
            raise RuntimeStepError("controlled failure", retryable=False)
        return _runtime_payload()

    runtime, store = _test_engine(tmp_path, handler)
    service = KnowledgeExtractionService(repository, runtime)
    base = _logical_request_id(source)
    with pytest.raises(KnowledgeExtractionError):
        service.extract(source, structured)
    original = store.get_task_by_request_id(base)
    first = ExtractionRerunAuthorization(
        generation=1,
        failed_task_id=original.task_id,
        approved_by="kp-test-operator",
        reason="first recovery",
        approval_ref="APPROVAL-01",
    )
    with pytest.raises(KnowledgeExtractionError):
        service.extract(source, structured, rerun_authorization=first)
    rerun_one = store.get_task_by_request_id(base + ":rerun:1")
    assert rerun_one.status == RuntimeStatus.FAILED
    second = ExtractionRerunAuthorization(
        generation=2,
        failed_task_id=rerun_one.task_id,
        approved_by="kp-test-operator",
        reason="second explicitly approved recovery",
        approval_ref="APPROVAL-02",
    )
    assert len(service.extract(
        source, structured, rerun_authorization=second
    )) == 4
    assert len(calls) == 3
    assert store.get_task_by_request_id(base + ":rerun:2").status == RuntimeStatus.COMPLETED
    assert store.get_task_by_request_id(base).status == RuntimeStatus.FAILED
    assert store.get_task_by_request_id(base + ":rerun:1").status == RuntimeStatus.FAILED
