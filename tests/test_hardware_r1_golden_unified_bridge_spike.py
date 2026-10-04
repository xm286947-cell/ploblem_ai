from __future__ import annotations

import copy
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pytest
from fastapi.testclient import TestClient

from knowledge_production import KnowledgeReleaseService, create_knowledge_api_app
from repositories import JsonArtifactRepository
from services.hardware_case_knowledge_adapter import HardwareCaseKnowledgeAdapter
from services.hardware_case_source_store import (
    HardwareCaseSourceError,
    HardwareCaseSourceStore,
)
from services.hardware_r1_golden_knowledge_bridge import (
    FIXED_KNOWLEDGE_REVISION,
    HardwareR1GoldenBridgeError,
    HardwareR1GoldenKnowledgeBridge,
)


NOW = datetime(2026, 10, 3, 5, 30, tzinfo=timezone.utc)
RELEASE = "HARDWARE-R1-GOLDEN-BRIDGE-SPIKE-R1"


class ClientTransport:
    def __init__(self, client: TestClient):
        self.client = client

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, Any] | None = None,
        query: Mapping[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        response = self.client.request(
            method,
            path,
            json=dict(json_body) if json_body is not None else None,
            params=dict(query) if query is not None else None,
        )
        payload = response.json()
        assert isinstance(payload, dict)
        return response.status_code, payload


def knowledge_adapter(tmp_path: Path):
    root = tmp_path / "knowledge"
    app = create_knowledge_api_app(
        str(root),
        knowledge_release_version=RELEASE,
        service_id="hardware_r1_golden_bridge_spike",
    )
    adapter = HardwareCaseKnowledgeAdapter(
        ClientTransport(TestClient(app)),
        knowledge_release_version=RELEASE,
    )
    return root, adapter


def golden(case_id: str, source_id: str) -> dict[str, Any]:
    block = "B0007" if case_id == "A0207" else "B0004"
    root_cause = (
        "参考源精度偏差导致模拟量输出偏差"
        if case_id == "A0207"
        else "TX弱上拉驱动能力不足"
    )
    return {
        "contract_version": "hardware-case-knowledge-object/v1",
        "identity": {
            "business_case_id": case_id,
            "raw_title": f"{case_id}-synthetic",
            "identity_status": "PARSED",
        },
        "source_fact": {
            "source_id": source_id,
            "business_case_id": case_id,
            "raw_title": f"{case_id}-synthetic",
            "original_filename": f"{case_id}-synthetic.docx",
            "markdown_view": {
                "view_version": "hardware-markdown-view/v1",
                "markdown": root_cause,
            },
            "snapshot_version": "hardware-document-snapshot/v1",
            "source_locators": [
                {
                    "block_id": block,
                    "source_locator": {"paragraph": 4, "block_id": block},
                }
            ],
        },
        "engineering_context": {
            "primary_subject": {
                "value": "MCU" if case_id == "A0152" else "ADC参考源",
                "extraction_status": "EXTRACTED",
                "evidence_block_ids": [block],
            }
        },
        "observed_problem": {
            "symptom": {
                "value": "通信乱码" if case_id == "A0152" else "模拟量输出偏差",
                "extraction_status": "EXTRACTED",
                "evidence_block_ids": [block],
            }
        },
        "engineering_analysis": {
            "root_cause": {
                "value": root_cause,
                "extraction_status": "EXTRACTED",
                "evidence_block_ids": [block],
            }
        },
        "engineering_resolution": {
            "actions": {
                "value": "调整输出/参考源设计",
                "extraction_status": "EXTRACTED",
                "evidence_block_ids": [block],
            }
        },
        "reusable_knowledge": {
            "engineering_rule": {
                "value": "设计必须校核接口电气裕量",
                "extraction_status": "EXTRACTED",
                "evidence_block_ids": [block],
                "derived_from_fields": ["facts.root_cause"],
                "review_status": "UNREVIEWED",
            }
        },
        "evidence": [
            {
                "block_id": block,
                "block_type": "PARAGRAPH",
                "text": root_cause,
                "image_ref": None,
                "source_locator": {"paragraph": 4, "block_id": block},
            }
        ],
        "conflicts": [],
        "review": {
            "object_status": "CANDIDATE",
            "reviewer": None,
            "reviewed_at": None,
            "field_decisions": [],
        },
        "provenance": {
            "source_id": source_id,
            "snapshot_version": "hardware-document-snapshot/v1",
            "markdown_version": "hardware-markdown-view/v1",
            "agent_id": "hardware_case.r1_extract",
            "agent_config_version": "v1.5.1",
            "runtime_run_id": f"run-{case_id.lower()}",
            "extraction_contract_version": "hardware-r1-extraction/v2",
        },
    }


def evidence_gate() -> dict[str, Any]:
    return {
        "status": "PASS",
        "fabricated_fact_count": 0,
        "fabricated_block_id_count": 0,
    }


def setup_case(tmp_path: Path, case_id: str):
    payload = f"synthetic-{case_id}".encode()
    source_store = HardwareCaseSourceStore(
        tmp_path / f"{case_id}.db",
        tmp_path / f"{case_id}-sources",
    )
    source = source_store.register_active_bytes(
        case_id,
        f"{case_id}-synthetic.docx",
        payload,
        mime_type=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
    )
    root, adapter = knowledge_adapter(tmp_path / case_id)
    bridge = HardwareR1GoldenKnowledgeBridge(adapter, source_store)
    return root, source_store, bridge, source


@pytest.mark.parametrize("case_id", ["A0152", "A0207"])
def test_golden_candidate_intake_has_no_semantic_loss(
    tmp_path: Path,
    case_id: str,
) -> None:
    _, _, bridge, source = setup_case(tmp_path, case_id)
    source_id = hashlib.sha256(f"synthetic-{case_id}".encode()).hexdigest()
    assert source["source_id"] == source_id

    payload = golden(case_id, source_id)
    result = bridge.intake(payload, evidence_gate())

    candidate = result["candidate"]
    assert result["status"] == "CANDIDATE_INTAKE_PASS"
    assert candidate["structured_content"] == payload
    assert candidate["revision"] == FIXED_KNOWLEDGE_REVISION
    assert candidate["evidence_refs"] == [
        bridge.evidence_id(case_id, source_id, payload["evidence"][0]["block_id"])
    ]


def test_human_edit_is_preserved_and_source_fact_is_immutable(tmp_path: Path) -> None:
    _, _, bridge, source = setup_case(tmp_path, "A0152")
    original = golden("A0152", source["source_id"])
    intake = bridge.intake(original, evidence_gate())
    candidate_id = intake["candidate"]["candidate_id"]

    confirmed = copy.deepcopy(original)
    confirmed["reusable_knowledge"]["engineering_rule"]["value"] = (
        "数字输出设计需校核驱动能力与实际负载"
    )
    review = bridge.review(
        candidate_id=candidate_id,
        original_golden=original,
        confirmed_content=confirmed,
        reviewer="hardware-reviewer",
        review_time=NOW,
    )
    assert review["review_status"] == "CONFIRMED"
    assert review["confirmed_value"] == confirmed

    invalid = copy.deepcopy(confirmed)
    invalid["source_fact"]["raw_title"] = "rewritten-source"
    with pytest.raises(HardwareR1GoldenBridgeError, match="SOURCE_FACT_IMMUTABLE"):
        bridge.review(
            candidate_id=candidate_id,
            original_golden=original,
            confirmed_content=invalid,
            reviewer="hardware-reviewer",
            review_time=NOW,
        )


def test_publish_is_idempotent_locks_source_and_query_back_traces_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, source_store, bridge, source = setup_case(tmp_path, "A0207")
    original = golden("A0207", source["source_id"])
    intake = bridge.intake(original, evidence_gate())
    candidate = intake["candidate"]
    confirmed = copy.deepcopy(original)
    confirmed["reusable_knowledge"]["engineering_rule"]["value"] = (
        "模拟量精度设计必须校核参考源与器件误差预算"
    )
    bridge.review(
        candidate_id=candidate["candidate_id"],
        original_golden=original,
        confirmed_content=confirmed,
        reviewer="hardware-reviewer",
        review_time=NOW,
        review_comment="spike confirmed",
    )

    evidence_refs = list(candidate["evidence_refs"])
    first = bridge.publish(
        business_case_id="A0207",
        candidate_id=candidate["candidate_id"],
        evidence_refs=evidence_refs,
        publisher="hardware-publisher",
        published_at=NOW,
    )

    idempotency_key = bridge.adapter.publish_idempotency_key(
        candidate["candidate_id"], FIXED_KNOWLEDGE_REVISION
    )
    assert idempotency_key == "HC-KNOWLEDGE-A0207-R1:publish:r1"
    assert first["idempotency_key"] == idempotency_key
    marker_directory = root / "knowledge/production/public/idempotency/publish"
    legacy_marker_relative_path = (
        "knowledge/production/public/idempotency/publish/"
        f"{idempotency_key}.json"
    )
    safe_marker_path = marker_directory / (
        hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest() + ".json"
    )
    legacy_marker_path = marker_directory / f"{idempotency_key}.json"
    assert safe_marker_path.is_file()
    marker_payload = json.loads(safe_marker_path.read_text(encoding="utf-8"))
    assert marker_payload["idempotency_key"] == idempotency_key
    if os.name != "nt":
        assert not legacy_marker_path.exists()

    # A marker at the hashed path must be bound to the exact business key.
    safe_marker_path.write_text(
        json.dumps({**marker_payload, "idempotency_key": "different-key"}),
        encoding="utf-8",
    )
    with pytest.raises(HardwareR1GoldenBridgeError, match="IDEMPOTENCY_KEY_CONFLICT"):
        bridge.publish(
            business_case_id="A0207",
            candidate_id=candidate["candidate_id"],
            evidence_refs=evidence_refs,
            publisher="hardware-publisher",
            published_at=NOW,
        )

    safe_marker_path.write_text(
        json.dumps(marker_payload),
        encoding="utf-8",
    )
    safe_marker_path.unlink()
    if os.name == "nt":
        # Windows cannot create the old colon-containing filename. Simulate a
        # pre-upgrade POSIX marker at the repository boundary instead.
        repository = (
            bridge.adapter.transport.client.app.state.public_knowledge.repository
        )
        original_load = repository.load
        legacy_lookups: list[str] = []

        def load_with_legacy_marker(path: str | Path, *, required: bool = False):
            path_text = str(path)
            if path_text == legacy_marker_relative_path:
                legacy_lookups.append(path_text)
                return marker_payload
            return original_load(path, required=required)

        monkeypatch.setattr(repository, "load", load_with_legacy_marker)
    else:
        legacy_marker_path.write_text(
            json.dumps(marker_payload),
            encoding="utf-8",
        )

    second = bridge.publish(
        business_case_id="A0207",
        candidate_id=candidate["candidate_id"],
        evidence_refs=evidence_refs,
        publisher="hardware-publisher",
        published_at=NOW,
    )
    assert first["publish_status"] == "PUBLISHED"
    assert (
        first["object"]["knowledge_id"]
        == second["object"]["knowledge_id"]
    )
    assert not safe_marker_path.exists()
    if os.name == "nt":
        assert legacy_lookups == [legacy_marker_relative_path]
    else:
        assert legacy_marker_path.is_file()
    assert source_store.formal_knowledge_references("A0207")

    with pytest.raises(
        HardwareCaseSourceError,
        match="SOURCE_IN_USE_BY_FORMAL_KNOWLEDGE",
    ):
        source_store.delete_active_source("A0207")

    KnowledgeReleaseService(JsonArtifactRepository(root)).build(
        RELEASE,
        created_at=NOW,
    )
    queried = bridge.query_back("A0207")
    assert queried["object"]["revision"] == 1
    assert queried["object"]["content"] == confirmed
    assert queried["object"]["evidence_refs"] == evidence_refs
    assert queried["evidences"][0]["source"]["source_id"] == source["source_id"]


def test_image_evidence_is_explicitly_deferred(tmp_path: Path) -> None:
    _, _, bridge, source = setup_case(tmp_path, "A0152")
    payload = golden("A0152", source["source_id"])
    payload["evidence"][0]["block_type"] = "IMAGE"
    with pytest.raises(HardwareR1GoldenBridgeError, match="IMAGE_EVIDENCE_DEFERRED"):
        bridge.precheck(payload, evidence_gate())
