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
    ExtractionRerunApproval,
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
    LightweightExecutionEngine,
    RuntimeStatus,
    SqliteTaskStore,
)
from runtime.reliability import RuntimeStepError
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


def _failed_extraction_runtime(tmp_path: Path):
    """Run the real persistent Runtime with a controllable, local-only handler."""
    calls = {"count": 0}
    runtime = LightweightExecutionEngine(SqliteTaskStore(tmp_path / "runtime.db"))

    def handler(_payload, _context):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeStepError(
                "simulated upstream failure",
                code="PROVIDER_HTTP_ERROR",
                retryable=False,
            )
        return _runtime_payload()

    runtime.register_agent(KnowledgeExtractionService.AGENT_ID, handler)
    return runtime, calls


def _approve_rerun(
    *,
    previous_task_id: str,
    generation: int = 1,
    approval_ref: str = "KP-475-APPROVED-001",
) -> ExtractionRerunApproval:
    return ExtractionRerunApproval(
        generation=generation,
        previous_task_id=previous_task_id,
        approved_by="approved-test-operator",
        approval_ref=approval_ref,
        reason="provider configuration restored",
    )


def test_kp_m03_failed_task_rerun_is_explicit_audited_and_idempotent(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path / "repo")
    source, structured = _seed_source(repository)
    runtime, calls = _failed_extraction_runtime(tmp_path)
    service = KnowledgeExtractionService(repository, runtime)

    with pytest.raises(KnowledgeExtractionError, match="KNOWLEDGE_EXTRACTION_FAILED"):
        service.extract(source, structured)
    assert calls["count"] == 1

    old_request_id = next(
        row.request_id
        for row in [runtime.store.get_task_by_request_id(
            f"knowledge-extract:{source.source_id}:{source.source_version}:"
            f"{source.content_hash[:12]}:"
            f"{hashlib.sha256(b'[]').hexdigest()[:12]}"
        )]
        if row is not None
    )
    old_task = runtime.store.get_task_by_request_id(old_request_id)
    assert old_task is not None
    assert old_task.status == RuntimeStatus.FAILED

    # Ordinary duplicate submissions preserve the old failed Task and make no calls.
    with pytest.raises(KnowledgeExtractionError, match="KNOWLEDGE_EXTRACTION_FAILED"):
        service.extract(source, structured)
    assert calls["count"] == 1

    approval = _approve_rerun(previous_task_id=old_task.task_id)
    candidates = service.extract(source, structured, rerun_approval=approval)
    assert len(candidates) == 4
    assert calls["count"] == 2

    rerun_request_id = old_request_id + ":rerun:1"
    new_task = runtime.store.get_task_by_request_id(rerun_request_id)
    assert new_task is not None
    assert new_task.task_id != old_task.task_id
    assert new_task.input_hash == old_task.input_hash
    assert new_task.status == RuntimeStatus.COMPLETED
    assert runtime.get_task(old_task.task_id).status == RuntimeStatus.FAILED

    lineage = new_task.metadata["extraction_rerun"]
    assert lineage["logical_request_id"] == old_request_id
    assert lineage["generation"] == 1
    assert lineage["previous_task_id"] == old_task.task_id
    assert lineage["previous_request_id"] == old_request_id
    assert lineage["approval_ref"] == "KP-475-APPROVED-001"

    # Replaying the same authorized generation does not create an extra Task
    # or call the provider; candidate and evidence IDs remain identical.
    replay = service.extract(source, structured, rerun_approval=approval)
    assert calls["count"] == 2
    assert [x.candidate_id for x in replay] == [x.candidate_id for x in candidates]
    assert runtime.store.get_task_by_request_id(rerun_request_id).task_id == new_task.task_id
    assert len(repository.list("knowledge/production/candidates")) == 4
    assert len(repository.list("knowledge/production/evidence")) == 2
    assert repository.list("knowledge/production/published") == []


def test_kp_m03_rerun_fails_closed_for_bad_approval_and_predecessor(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path / "repo")
    source, structured = _seed_source(repository)
    runtime, calls = _failed_extraction_runtime(tmp_path)
    service = KnowledgeExtractionService(repository, runtime)

    # Missing/incorrect operator approval never reaches Runtime.
    for approval in [
        _approve_rerun(previous_task_id="does-not-exist"),
        _approve_rerun(previous_task_id="does-not-exist", generation=2),
        _approve_rerun(previous_task_id="does-not-exist", generation=0),
        _approve_rerun(previous_task_id="does-not-exist", generation=True),
        ExtractionRerunApproval(
            generation=1,
            previous_task_id="does-not-exist",
            approved_by="",
            approval_ref="approval",
            reason="test",
        ),
    ]:
        with pytest.raises(KnowledgeExtractionError, match="EXTRACTION_RERUN_"):
            service.extract(source, structured, rerun_approval=approval)
    assert calls["count"] == 0

    with pytest.raises(KnowledgeExtractionError, match="KNOWLEDGE_EXTRACTION_FAILED"):
        service.extract(source, structured)
    old_request = (
        f"knowledge-extract:{source.source_id}:{source.source_version}:"
        f"{source.content_hash[:12]}:"
        f"{hashlib.sha256(b'[]').hexdigest()[:12]}"
    )
    old_task = runtime.store.get_task_by_request_id(old_request)
    assert old_task is not None

    # A different source/topic or an unauthorized generation cannot reuse the old approval.
    with pytest.raises(KnowledgeExtractionError, match="EXTRACTION_RERUN_PREDECESSOR_INVALID"):
        service.extract(
            source, structured, requested_topics=["DEVICE HEALTH"],
            rerun_approval=_approve_rerun(previous_task_id=old_task.task_id)
        )
    with pytest.raises(KnowledgeExtractionError, match="EXTRACTION_RERUN_PREDECESSOR_INVALID"):
        service.extract(
            source, structured,
            rerun_approval=_approve_rerun(previous_task_id=old_task.task_id, generation=2)
        )
    assert calls["count"] == 1

    service.extract(
        source, structured,
        rerun_approval=_approve_rerun(previous_task_id=old_task.task_id)
    )
    assert calls["count"] == 2
    # A completed previous generation cannot be rerun again.
    successful = runtime.store.get_task_by_request_id(old_request + ":rerun:1")
    with pytest.raises(KnowledgeExtractionError, match="EXTRACTION_RERUN_PREDECESSOR_INVALID"):
        service.extract(
            source, structured,
            rerun_approval=_approve_rerun(
                previous_task_id=successful.task_id, generation=2,
            )
        )
    assert calls["count"] == 2


def test_kp_m03_rerun_same_generation_conflicting_authority_fails_closed(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path / "repo")
    source, structured = _seed_source(repository)
    runtime, calls = _failed_extraction_runtime(tmp_path)
    service = KnowledgeExtractionService(repository, runtime)

    with pytest.raises(KnowledgeExtractionError):
        service.extract(source, structured)
    old_request = (
        f"knowledge-extract:{source.source_id}:{source.source_version}:"
        f"{source.content_hash[:12]}:"
        f"{hashlib.sha256(b'[]').hexdigest()[:12]}"
    )
    old_task = runtime.store.get_task_by_request_id(old_request)
    service.extract(source, structured, rerun_approval=_approve_rerun(
        previous_task_id=old_task.task_id,
    ))
    assert calls["count"] == 2

    # The Runtime fingerprint detects changed authorization for the same
    # request_id. The KP boundary converts the conflict into a stable error.
    with pytest.raises(KnowledgeExtractionError, match="KNOWLEDGE_EXTRACTION_FAILED"):
        service.extract(source, structured, rerun_approval=_approve_rerun(
            previous_task_id=old_task.task_id,
            approval_ref="DIFFERENT-APPROVAL",
        ))
    assert calls["count"] == 2


def test_kp_m03_concurrent_same_rerun_generation_invokes_once(
    tmp_path: Path,
) -> None:
    repository = JsonArtifactRepository(tmp_path / "repo")
    source, structured = _seed_source(repository)
    runtime = LightweightExecutionEngine(SqliteTaskStore(tmp_path / "runtime.db"))
    rerun_entered = threading.Event()
    allow_completion = threading.Event()
    calls = {"count": 0}

    def handler(_payload, _context):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeStepError(
                "simulated upstream failure",
                code="PROVIDER_HTTP_ERROR",
                retryable=False,
            )
        rerun_entered.set()
        assert allow_completion.wait(10), "test unblock timeout"
        return _runtime_payload()

    runtime.register_agent(KnowledgeExtractionService.AGENT_ID, handler)
    service = KnowledgeExtractionService(repository, runtime)

    with pytest.raises(KnowledgeExtractionError):
        service.extract(source, structured)
    old_request = (
        f"knowledge-extract:{source.source_id}:{source.source_version}:"
        f"{source.content_hash[:12]}:"
        f"{hashlib.sha256(b'[]').hexdigest()[:12]}"
    )
    failed = runtime.store.get_task_by_request_id(old_request)
    assert failed is not None
    approval = _approve_rerun(previous_task_id=failed.task_id)
    results: list[list] = []
    errors: list[Exception] = []

    def first_rerun():
        try:
            results.append(service.extract(
                source, structured, rerun_approval=approval
            ))
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=first_rerun, daemon=True)
    worker.start()
    try:
        assert rerun_entered.wait(10), "first rerun did not enter the handler"
        # The second request may observe the in-flight Task and fail closed;
        # it must not dispatch an additional provider call.
        with pytest.raises(KnowledgeExtractionError, match="KNOWLEDGE_EXTRACTION_FAILED"):
            service.extract(source, structured, rerun_approval=approval)
        assert calls["count"] == 2
    finally:
        allow_completion.set()
        worker.join(timeout=10)

    assert not worker.is_alive()
    assert errors == []
    assert len(results) == 1
    assert len(results[0]) == 4
    assert calls["count"] == 2
    assert runtime.store.get_task_by_request_id(
        old_request + ":rerun:1"
    ).status == RuntimeStatus.COMPLETED
