from __future__ import annotations

import ast
import hashlib
import inspect
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pytest
from fastapi.testclient import TestClient

import services.hardware_case_knowledge_adapter as adapter_module
from knowledge_production import KnowledgeReleaseService, create_knowledge_api_app
from repositories import JsonArtifactRepository
from repositories.hardware_case_repository import HardwareCaseRepository
from services.hardware_case_backend import HardwareCaseBackendService
from services.hardware_case_knowledge_adapter import (
    CONTRACT_VERSIONS,
    KNOWLEDGE_CAPABILITY_VERSION,
    KNOWLEDGE_MAIN_COMMIT,
    KNOWLEDGE_SHA256,
    KNOWLEDGE_SOURCE_COMMIT,
    HardwareCaseKnowledgeAdapter,
    HardwareKnowledgeAdapterError,
)


NOW = datetime(2026, 9, 24, 14, 0, tzinfo=timezone.utc)
RELEASE = "HC-KNOWLEDGE-ADAPTER-CONTRACT-GATE-V1"
CASE_ID = "HC-KNOWLEDGE-001"
SOURCE_TEXT = "输入浪涌触发保护并导致控制器反复复位。"


class ClientTransport:
    def __init__(self, client: TestClient):
        self.client = client
        self.calls: list[dict[str, Any]] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, Any] | None = None,
        query: Mapping[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        self.calls.append(
            {
                "method": method,
                "path": path,
                "json_body": dict(json_body) if json_body is not None else None,
                "query": dict(query) if query is not None else None,
            }
        )
        response = self.client.request(
            method,
            path,
            json=dict(json_body) if json_body is not None else None,
            params=dict(query) if query is not None else None,
        )
        value = response.json()
        assert isinstance(value, dict)
        return response.status_code, value


class UnavailableTransport:
    def request(self, *args, **kwargs):
        raise OSError("synthetic unavailable")


def _knowledge(tmp_path: Path):
    root = tmp_path / "knowledge-repository"
    app = create_knowledge_api_app(
        str(root),
        knowledge_release_version=RELEASE,
        service_id="hardware_case_knowledge_contract_gate",
    )
    client = TestClient(app)
    transport = ClientTransport(client)
    adapter = HardwareCaseKnowledgeAdapter(
        transport,
        knowledge_release_version=RELEASE,
    )
    return root, client, transport, adapter


def _source_metadata() -> dict[str, Any]:
    return {
        "source_id": "SRC-HW-WORD-001",
        "sha256": hashlib.sha256(b"synthetic-word-source").hexdigest(),
        "mime_type": (
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
    }


def _hardware_evidence(
    *,
    evidence_id: str = "EV-HW-001",
    source_ref: str = "word:synthetic-hardware-case.docx",
) -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "case_id": CASE_ID,
        "source_ref": source_ref,
        "evidence_type": "TEXT",
        "locator": {
            "section": "原因分析",
            "paragraph": 8,
            "block_id": "B0008",
        },
        "excerpt_or_caption": SOURCE_TEXT,
        "evidence_status": "AVAILABLE",
    }


def _ai_structured() -> dict[str, Any]:
    return {
        "hardware_case_id": CASE_ID,
        "title": "Synthetic startup reset",
        "product_context": {
            "product": "Synthetic Controller",
            "device": "PMIC",
        },
        "facts": {
            "symptom": {
                "value": "控制器反复复位",
                "evidence_block_ids": ["B0008"],
            },
            "root_cause": {
                "value": "输入浪涌触发保护",
                "evidence_block_ids": ["B0008"],
            },
            "actions": {
                "value": "增加输入保护与浪涌抑制",
                "evidence_block_ids": ["B0008"],
            },
        },
        "circuit_feature_links": [
            {
                "node_id": "CF-POWER",
                "evidence_block_ids": ["B0008"],
            }
        ],
        "material_links": [],
    }


def _human_confirmed() -> dict[str, Any]:
    value = _ai_structured()
    value = json.loads(json.dumps(value, ensure_ascii=False))
    value["facts"]["actions"]["value"] = "增加TVS并校核输入浪涌裕量"
    return value


def _hardware_gate(tmp_path: Path, *, passed: bool) -> dict[str, Any]:
    backend = HardwareCaseBackendService(
        HardwareCaseRepository(tmp_path / "hardware.sqlite3")
    )
    backend.create_case(
        {
            "case_id": CASE_ID,
            "title": "Synthetic startup reset",
            "case_status": "PENDING_REVIEW",
            "processing_status": "READY",
            "source_refs": ["word:synthetic-hardware-case.docx"],
            "product_context": {"product": "Synthetic Controller"},
            "facts": {
                name: {
                    "candidate_value": value,
                    "confirmed_value": None,
                    "review_disposition": "UNREVIEWED",
                    "evidence_refs": ["EV-HW-001"],
                }
                for name, value in {
                    "symptom": "控制器反复复位",
                    "root_cause": "输入浪涌触发保护",
                    "actions": "增加输入保护与浪涌抑制",
                }.items()
            },
        }
    )
    backend.save_evidence(_hardware_evidence())
    backend.save_tree_node(
        {
            "node_id": "CF-POWER",
            "tree_type": "CIRCUIT_FEATURE",
            "name": "输入保护",
            "path": ["电源", "输入保护"],
            "active": True,
        }
    )
    backend.set_mapping(
        {
            "mapping_id": "MAP-KNOWLEDGE-1",
            "case_id": CASE_ID,
            "tree_type": "CIRCUIT_FEATURE",
            "node_id": "CF-POWER",
            "relation_role": "PRIMARY",
            "mapping_status": "CONFIRMED",
            "confidence": 1.0,
            "basis_refs": ["EV-HW-001"],
        }
    )
    if passed:
        for field_name, value in {
            "symptom": "控制器反复复位",
            "root_cause": "输入浪涌触发保护",
            "actions": "增加输入保护与浪涌抑制",
        }.items():
            backend.review_case(
                CASE_ID,
                field_name,
                disposition="CONFIRMED",
                confirmed_value=value,
            )
    return backend.check_publish_gate(CASE_ID)


def _register_evidence_and_candidate(
    adapter: HardwareCaseKnowledgeAdapter,
    *,
    case_id: str = CASE_ID,
    revision: int = 1,
    structured_content: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    evidence = adapter.intake_evidence(
        case_id=case_id,
        source_metadata=_source_metadata(),
        evidence=_hardware_evidence(),
        revision=revision,
        source_revision=f"V{revision}",
    )
    candidate = adapter.intake_candidate(
        case_id=case_id,
        source_document_id=_source_metadata()["source_id"],
        source_ref=_hardware_evidence()["source_ref"],
        structured_content=structured_content or _ai_structured(),
        evidence_refs=[evidence["evidence_id"]],
        revision=revision,
        source_version=f"V{revision}",
    )
    return evidence, candidate


def _publish_and_release(tmp_path: Path):
    root, _, transport, adapter = _knowledge(tmp_path)
    evidence, candidate = _register_evidence_and_candidate(adapter)
    pending = adapter.review_candidate(
        candidate_id=candidate["candidate_id"],
        state="PENDING_REVIEW",
        reviewer="reviewer",
        review_time=NOW,
        revision=1,
    )
    assert pending["review_status"] == "PENDING_REVIEW"

    review = adapter.review_candidate(
        candidate_id=candidate["candidate_id"],
        state="CONFIRMED",
        reviewer="reviewer",
        review_time=NOW,
        review_comment="Hardware review complete",
        confirmed_content=_human_confirmed(),
        revision=1,
    )
    assert review["review_status"] == "CONFIRMED"

    gate = _hardware_gate(tmp_path, passed=True)
    publish = adapter.publish(
        candidate_id=candidate["candidate_id"],
        hardware_publish_gate=gate,
        evidence_refs=[evidence["evidence_id"]],
        publisher="hardware-publisher",
        published_at=NOW,
        revision=1,
    )
    KnowledgeReleaseService(JsonArtifactRepository(root)).build(
        RELEASE,
        created_at=NOW,
    )
    return root, transport, adapter, evidence, candidate, publish


def test_release_descriptor_and_contract_versions_are_exactly_frozen():
    assert KNOWLEDGE_CAPABILITY_VERSION == "KNOWLEDGE_CAPABILITY_RELEASE_V0.3"
    assert KNOWLEDGE_SOURCE_COMMIT == (
        "92ef3f3c4ec80c4987f2b715dcd2d485eda8e372"
    )
    assert KNOWLEDGE_MAIN_COMMIT == (
        "1ed6dc186637049afaa98c28ea2c8042c63588ae"
    )
    assert KNOWLEDGE_SHA256 == (
        "2e73cf0cf10a92a33378f72546e76204918ff756c1afc466d4de017c67933550"
    )
    assert CONTRACT_VERSIONS == {
        "candidate": "knowledge-candidate/v1",
        "evidence": "knowledge-evidence/v1",
        "review": "knowledge-review/v1",
        "publish": "knowledge-publish/v1",
        "object": "knowledge-object/v1",
        "query": "knowledge-query/v1",
    }


def test_adapter_has_no_direct_knowledge_runtime_or_repository_imports():
    source = inspect.getsource(adapter_module)
    tree = ast.parse(source)
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    assert not any(name.startswith("knowledge_production") for name in imported)
    assert not any(name.startswith("repositories") for name in imported)
    assert not any(name.startswith("runtime") for name in imported)


def test_cg01_candidate_preserves_domain_type_content_source_and_revision(tmp_path):
    _, _, transport, adapter = _knowledge(tmp_path)
    evidence, candidate = _register_evidence_and_candidate(adapter)

    assert candidate["domain"] == "HARDWARE_CASE"
    assert candidate["object_type"] == "HARDWARE_CASE"
    assert candidate["structured_content"] == _ai_structured()
    assert candidate["evidence_refs"] == [evidence["evidence_id"]]
    assert candidate["revision"] == 1

    candidate_call = next(
        item for item in transport.calls
        if item["path"] == "/v1/knowledge/candidates"
    )
    assert candidate_call["json_body"]["metadata"]["hardware_case_id"] == CASE_ID
    assert (
        candidate_call["json_body"]["metadata"]["hardware_source_ref"]
        == "word:synthetic-hardware-case.docx"
    )


def test_cg02_evidence_resolves_to_original_word_reference(tmp_path):
    root, _, _, adapter = _knowledge(tmp_path)
    evidence, candidate = _register_evidence_and_candidate(adapter)
    adapter.review_candidate(
        candidate_id=candidate["candidate_id"],
        state="CONFIRMED",
        reviewer="reviewer",
        review_time=NOW,
        confirmed_content=_human_confirmed(),
        revision=1,
    )
    publish = adapter.publish(
        candidate_id=candidate["candidate_id"],
        hardware_publish_gate=_hardware_gate(tmp_path, passed=True),
        evidence_refs=[evidence["evidence_id"]],
        publisher="publisher",
        published_at=NOW,
        revision=1,
    )
    KnowledgeReleaseService(JsonArtifactRepository(root)).build(
        RELEASE, created_at=NOW
    )

    resolved = adapter.resolve_evidence(evidence["evidence_id"])
    assert resolved["source"]["source_id"] == _source_metadata()["source_id"]
    assert resolved["source"]["uri"] == "word:synthetic-hardware-case.docx"
    assert resolved["excerpt"] == SOURCE_TEXT
    assert resolved["source"]["source_type"] == "WORD"
    assert resolved["source"]["metadata"]["hardware_locator"]["block_id"] == "B0008"
    assert publish["object"]["evidence_refs"] == [evidence["evidence_id"]]


def test_cg03_pending_confirm_and_human_value_remain_separate(tmp_path):
    root, _, _, adapter = _knowledge(tmp_path)
    _, candidate = _register_evidence_and_candidate(adapter)
    pending = adapter.review_candidate(
        candidate_id=candidate["candidate_id"],
        state="PENDING_REVIEW",
        reviewer="reviewer",
        review_time=NOW,
        revision=1,
    )
    assert pending["review_status"] == "PENDING_REVIEW"

    confirmed = adapter.review_candidate(
        candidate_id=candidate["candidate_id"],
        state="CONFIRMED",
        reviewer="reviewer",
        review_time=NOW,
        confirmed_content=_human_confirmed(),
        revision=1,
    )
    assert confirmed["review_status"] == "CONFIRMED"
    assert confirmed["confirmed_value"] == _human_confirmed()

    raw = JsonArtifactRepository(root).load(
        "knowledge/production/candidates/"
        + candidate["candidate_id"]
        + ".json",
        required=True,
    )
    assert raw is not None
    assert json.loads(raw["content"]) == _ai_structured()


def test_cg03_reject_forbids_publish(tmp_path):
    _, _, _, adapter = _knowledge(tmp_path)
    evidence, candidate = _register_evidence_and_candidate(
        adapter,
        case_id="HC-REJECT-001",
    )
    rejected = adapter.review_candidate(
        candidate_id=candidate["candidate_id"],
        state="REJECTED",
        reviewer="reviewer",
        review_time=NOW,
        revision=1,
    )
    assert rejected["review_status"] == "REJECTED"

    with pytest.raises(
        HardwareKnowledgeAdapterError,
        match="PUBLISH_NOT_CONFIRMED",
    ):
        adapter.publish(
            candidate_id=candidate["candidate_id"],
            hardware_publish_gate={"passed": True},
            evidence_refs=[evidence["evidence_id"]],
            publisher="publisher",
            published_at=NOW,
            revision=1,
        )


def test_cg04_hardware_gate_precedes_knowledge_publish(tmp_path):
    _, _, transport, adapter = _knowledge(tmp_path)
    evidence, candidate = _register_evidence_and_candidate(adapter)
    adapter.review_candidate(
        candidate_id=candidate["candidate_id"],
        state="CONFIRMED",
        reviewer="reviewer",
        review_time=NOW,
        confirmed_content=_human_confirmed(),
        revision=1,
    )
    before = len(transport.calls)

    with pytest.raises(
        HardwareKnowledgeAdapterError,
        match="HARDWARE_PUBLISH_GATE_NOT_PASSED",
    ):
        adapter.publish(
            candidate_id=candidate["candidate_id"],
            hardware_publish_gate=_hardware_gate(tmp_path, passed=False),
            evidence_refs=[evidence["evidence_id"]],
            publisher="publisher",
            published_at=NOW,
            revision=1,
        )
    assert len(transport.calls) == before

    published = adapter.publish(
        candidate_id=candidate["candidate_id"],
        hardware_publish_gate=_hardware_gate(tmp_path, passed=True),
        evidence_refs=[evidence["evidence_id"]],
        publisher="publisher",
        published_at=NOW,
        revision=1,
    )
    assert published["publish_status"] == "PUBLISHED"


def test_cg04_missing_evidence_fails_closed_before_publish(tmp_path):
    _, _, transport, adapter = _knowledge(tmp_path)
    before = len(transport.calls)
    with pytest.raises(HardwareKnowledgeAdapterError, match="EVIDENCE_MISSING"):
        adapter.publish(
            candidate_id="HC-KNOWLEDGE-X-R1",
            hardware_publish_gate={"passed": True},
            evidence_refs=[],
            publisher="publisher",
            published_at=NOW,
            revision=1,
        )
    assert len(transport.calls) == before


def test_cg05_publish_is_idempotent(tmp_path):
    _, _, adapter, _, _, first = _publish_and_release(tmp_path)
    second = adapter.publish(
        candidate_id=first["object"]["candidate_ref"],
        hardware_publish_gate={"passed": True},
        evidence_refs=first["object"]["evidence_refs"],
        publisher="hardware-publisher",
        published_at=NOW,
        revision=1,
    )
    assert second["object"]["knowledge_id"] == first["object"]["knowledge_id"]
    assert second["object"]["revision"] == first["object"]["revision"]


def test_cg06_query_get_revision_status_and_evidence_refs(tmp_path):
    _, _, adapter, evidence, _, publish = _publish_and_release(tmp_path)
    knowledge_id = publish["object"]["knowledge_id"]

    result = adapter.search(text="TVS")
    assert len(result["objects"]) == 1
    assert result["objects"][0]["knowledge_id"] == knowledge_id

    obj = adapter.get_object(knowledge_id, expected_revision=1)
    assert obj["revision"] == 1
    assert obj["status"] == "ACTIVE"
    assert obj["evidence_refs"] == [evidence["evidence_id"]]
    assert obj["content"] == _human_confirmed()


def test_cg07_object_to_evidence_to_original_source(tmp_path):
    _, _, adapter, evidence, _, publish = _publish_and_release(tmp_path)
    obj = adapter.get_object(publish["object"]["knowledge_id"])
    resolved = adapter.resolve_evidence(obj["evidence_refs"][0])

    assert resolved["evidence_id"] == evidence["evidence_id"]
    assert resolved["source"]["uri"] == "word:synthetic-hardware-case.docx"
    assert resolved["excerpt"] == SOURCE_TEXT


def test_cg08_knowledge_unavailable_is_explicit():
    adapter = HardwareCaseKnowledgeAdapter(
        UnavailableTransport(),
        knowledge_release_version=RELEASE,
    )
    with pytest.raises(
        HardwareKnowledgeAdapterError,
        match="KNOWLEDGE_UNAVAILABLE",
    ):
        adapter.search()


def test_cg08_invalid_candidate_is_explicit(tmp_path):
    _, _, _, adapter = _knowledge(tmp_path)
    with pytest.raises(
        HardwareKnowledgeAdapterError,
        match="CANDIDATE_CONTRACT_INVALID",
    ):
        adapter.intake_candidate(
            case_id=CASE_ID,
            source_document_id="SRC",
            source_ref="word:x.docx",
            structured_content=[],  # type: ignore[arg-type]
            evidence_refs=[],
            revision=1,
        )


def test_cg08_missing_evidence_reference_is_explicit(tmp_path):
    _, _, _, adapter = _knowledge(tmp_path)
    with pytest.raises(
        HardwareKnowledgeAdapterError,
        match="EVIDENCE_MISSING",
    ):
        adapter.intake_candidate(
            case_id=CASE_ID,
            source_document_id="SRC",
            source_ref="word:x.docx",
            structured_content=_ai_structured(),
            evidence_refs=["EV-MISSING"],
            revision=1,
        )


def test_cg08_unconfirmed_review_is_explicit(tmp_path):
    _, _, _, adapter = _knowledge(tmp_path)
    evidence, candidate = _register_evidence_and_candidate(adapter)
    with pytest.raises(
        HardwareKnowledgeAdapterError,
        match="PUBLISH_NOT_CONFIRMED",
    ):
        adapter.publish(
            candidate_id=candidate["candidate_id"],
            hardware_publish_gate={"passed": True},
            evidence_refs=[evidence["evidence_id"]],
            publisher="publisher",
            published_at=NOW,
            revision=1,
        )


def test_cg08_not_found_revision_mismatch_and_missing_evidence(tmp_path):
    _, _, adapter, _, _, publish = _publish_and_release(tmp_path)

    with pytest.raises(
        HardwareKnowledgeAdapterError,
        match="KNOWLEDGE_OBJECT_NOT_FOUND",
    ):
        adapter.get_object("KO-NOT-FOUND")

    with pytest.raises(
        HardwareKnowledgeAdapterError,
        match="EVIDENCE_NOT_FOUND",
    ):
        adapter.resolve_evidence("EV-NOT-FOUND")

    with pytest.raises(
        HardwareKnowledgeAdapterError,
        match="KNOWLEDGE_REVISION_MISMATCH",
    ):
        adapter.get_object(
            publish["object"]["knowledge_id"],
            expected_revision=2,
        )
