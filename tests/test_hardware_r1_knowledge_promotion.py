from __future__ import annotations

import copy
import hashlib
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
    HardwareR1GoldenKnowledgeBridge,
)
from services.hardware_r1_knowledge_promotion import (
    HardwareR1KnowledgePromotionService,
    HardwareR1KnowledgePromotionStore,
    HardwareR1PromotionError,
)


NOW = datetime(2026, 10, 3, 6, 0, tzinfo=timezone.utc)
RELEASE = "HARDWARE-R1-FORMAL-PROMOTION-R1"


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
        body = response.json()
        assert isinstance(body, dict)
        return response.status_code, body


class FakeWorkbench:
    def __init__(self, items: list[dict[str, Any]]):
        self.items = {item["item_id"]: item for item in items}

    def get_item(self, item_id: str) -> dict[str, Any]:
        if item_id not in self.items:
            raise RuntimeError("BATCH_ITEM_NOT_FOUND")
        return self.items[item_id]

    def get_batch(self, batch_id: str) -> dict[str, Any]:
        selected = [
            item for item in self.items.values()
            if item["batch_id"] == batch_id
        ]
        if not selected:
            raise RuntimeError("BATCH_NOT_FOUND")
        return {"batch_id": batch_id, "items": selected}


def golden(case_id: str, source_id: str, block_id: str) -> dict[str, Any]:
    root_cause = (
        "TX弱上拉驱动能力不足"
        if case_id == "A0152"
        else "参考源精度偏差导致模拟量输出偏差"
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
                    "block_id": block_id,
                    "source_locator": {
                        "paragraph": 4,
                        "block_id": block_id,
                    },
                }
            ],
        },
        "engineering_context": {
            "primary_subject": {
                "value": "MCU" if case_id == "A0152" else "ADC参考源",
                "extraction_status": "EXTRACTED",
                "evidence_block_ids": [block_id],
            }
        },
        "observed_problem": {
            "symptom": {
                "value": "通信乱码" if case_id == "A0152" else "模拟量输出偏差",
                "extraction_status": "EXTRACTED",
                "evidence_block_ids": [block_id],
            }
        },
        "engineering_analysis": {
            "root_cause": {
                "value": root_cause,
                "extraction_status": "EXTRACTED",
                "evidence_block_ids": [block_id],
            }
        },
        "engineering_resolution": {
            "actions": {
                "value": "调整输出/参考源设计",
                "extraction_status": "EXTRACTED",
                "evidence_block_ids": [block_id],
            }
        },
        "reusable_knowledge": {
            "engineering_rule": {
                "value": "设计必须校核接口电气裕量",
                "extraction_status": "EXTRACTED",
                "evidence_block_ids": [block_id],
                "derived_from_fields": ["facts.root_cause"],
                "review_status": "UNREVIEWED",
            }
        },
        "evidence": [
            {
                "block_id": block_id,
                "block_type": "PARAGRAPH",
                "text": root_cause,
                "image_ref": None,
                "source_locator": {
                    "paragraph": 4,
                    "block_id": block_id,
                },
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


def validation(*, passed: bool = True) -> dict[str, Any]:
    return {
        "status": "PASS" if passed else "FAIL",
        "fabricated_fact_count": 0,
        "fabricated_block_id_count": 0,
    }


def setup_case(
    tmp_path: Path,
    *,
    case_id: str,
    item_id: str,
    batch_id: str,
):
    payload = f"source-{case_id}".encode()
    source_store = HardwareCaseSourceStore(
        tmp_path / "hardware.db",
        tmp_path / "sources",
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

    knowledge_root = tmp_path / "knowledge"
    knowledge_app = create_knowledge_api_app(
        str(knowledge_root),
        knowledge_release_version=RELEASE,
        service_id="hardware_r1_formal_promotion_test",
    )
    transport = ClientTransport(TestClient(knowledge_app))
    adapter = HardwareCaseKnowledgeAdapter(
        transport,
        knowledge_release_version=RELEASE,
    )
    bridge = HardwareR1GoldenKnowledgeBridge(adapter, source_store)

    candidate = golden(
        case_id,
        source["source_id"],
        "B0004" if case_id == "A0152" else "B0007",
    )
    item = {
        "item_id": item_id,
        "batch_id": batch_id,
        "business_case_id": case_id,
        "source_id": source["source_id"],
        "result": "CANDIDATE_READY",
        "candidate": candidate,
        "evidence_validation": validation(),
    }
    workbench = FakeWorkbench([item])
    promotion = HardwareR1KnowledgePromotionService(
        HardwareR1KnowledgePromotionStore(tmp_path / "hardware.db"),
        workbench_service=workbench,
        bridge=bridge,
    )
    return (
        promotion,
        workbench,
        source_store,
        source,
        knowledge_root,
        transport,
        candidate,
    )


def test_precheck_and_candidate_intake_are_item_idempotent_and_do_not_publish(
    tmp_path: Path,
) -> None:
    (
        promotion,
        _,
        _,
        source,
        _,
        transport,
        candidate,
    ) = setup_case(
        tmp_path,
        case_id="A0152",
        item_id="HWI-A0152",
        batch_id="HWB-1",
    )

    precheck = promotion.precheck_item("HWI-A0152")
    assert precheck["precheck"]["status"] == "PASS"
    assert precheck["promotion"]["source_id"] == source["source_id"]

    first = promotion.intake_item("HWI-A0152")
    calls_after_first = len(transport.calls)
    second = promotion.intake_item("HWI-A0152")

    assert first["status"] == "CANDIDATE_INTAKED"
    assert second["status"] == "CANDIDATE_INTAKED"
    assert second["idempotent_reuse"] is True
    assert len(transport.calls) == calls_after_first
    assert first["candidate_id"] == "HC-KNOWLEDGE-A0152-R1"
    assert first["evidence_refs"]
    assert not any(call["path"] == "/v1/knowledge/publish" for call in transport.calls)
    assert candidate["review"]["object_status"] == "CANDIDATE"


def test_human_review_publish_release_query_back_and_source_lock(
    tmp_path: Path,
) -> None:
    (
        promotion,
        _,
        source_store,
        source,
        knowledge_root,
        _,
        candidate,
    ) = setup_case(
        tmp_path,
        case_id="A0207",
        item_id="HWI-A0207",
        batch_id="HWB-2",
    )

    intake = promotion.intake_item("HWI-A0207")
    confirmed = copy.deepcopy(candidate)
    confirmed["reusable_knowledge"]["engineering_rule"]["value"] = (
        "模拟量精度设计必须校核参考源与器件误差预算"
    )
    reviewed = promotion.review_item(
        "HWI-A0207",
        reviewer="hardware-reviewer",
        confirmed_content=confirmed,
        review_time=NOW,
        review_comment="R1 formal promotion review",
    )
    assert reviewed["status"] == "REVIEW_CONFIRMED"

    published = promotion.publish_item(
        "HWI-A0207",
        publisher="hardware-publisher",
        published_at=NOW,
    )
    assert published["status"] == "PUBLISHED_PENDING_QUERY_BACK"
    assert published["knowledge_id"]
    assert published["evidence_refs"] == intake["evidence_refs"]

    # Query-back is deliberately separate from Publish. Unified Knowledge
    # release ownership stays outside Hardware R1.
    first_verify = promotion.verify_item("HWI-A0207")
    assert first_verify["status"] == "VERIFY_FAILED"
    assert first_verify["error_code"] == "KNOWLEDGE_RELEASE_NOT_FOUND"

    KnowledgeReleaseService(JsonArtifactRepository(knowledge_root)).build(
        RELEASE,
        created_at=NOW,
    )
    verified = promotion.verify_item("HWI-A0207", retry=True)
    assert verified["status"] == "VERIFIED"
    assert verified["retry_count"] == 1
    assert verified["public_ref"] == "HC-KNOWLEDGE-A0207-R1"

    refs = source_store.formal_knowledge_references("A0207")
    assert refs[0]["source_id"] == source["source_id"]
    assert refs[0]["knowledge_id"] == verified["knowledge_id"]

    with pytest.raises(
        HardwareCaseSourceError,
        match="SOURCE_IN_USE_BY_FORMAL_KNOWLEDGE",
    ):
        source_store.delete_active_source("A0207")


def test_publish_requires_explicit_human_review(tmp_path: Path) -> None:
    promotion, *_ = setup_case(
        tmp_path,
        case_id="A0152",
        item_id="HWI-REVIEW",
        batch_id="HWB-3",
    )
    promotion.intake_item("HWI-REVIEW")
    with pytest.raises(HardwareR1PromotionError, match="HUMAN_REVIEW_REQUIRED"):
        promotion.publish_item(
            "HWI-REVIEW",
            publisher="publisher",
            published_at=NOW,
        )


def test_batch_intake_isolates_not_ready_item(tmp_path: Path) -> None:
    payload_a = b"source-A0152"
    payload_b = b"source-A0207"
    source_store = HardwareCaseSourceStore(
        tmp_path / "hardware.db",
        tmp_path / "sources",
    )
    source_a = source_store.register_active_bytes(
        "A0152", "A0152.docx", payload_a
    )
    source_b = source_store.register_active_bytes(
        "A0207", "A0207.docx", payload_b
    )

    knowledge_root = tmp_path / "knowledge"
    app = create_knowledge_api_app(
        str(knowledge_root),
        knowledge_release_version=RELEASE,
        service_id="hardware_r1_batch_promotion_test",
    )
    adapter = HardwareCaseKnowledgeAdapter(
        ClientTransport(TestClient(app)),
        knowledge_release_version=RELEASE,
    )
    bridge = HardwareR1GoldenKnowledgeBridge(adapter, source_store)

    items = [
        {
            "item_id": "HWI-A",
            "batch_id": "HWB-BATCH",
            "business_case_id": "A0152",
            "source_id": source_a["source_id"],
            "result": "CANDIDATE_READY",
            "candidate": golden("A0152", source_a["source_id"], "B0004"),
            "evidence_validation": validation(),
        },
        {
            "item_id": "HWI-B",
            "batch_id": "HWB-BATCH",
            "business_case_id": "A0207",
            "source_id": source_b["source_id"],
            "result": "REVIEW",
            "candidate": golden("A0207", source_b["source_id"], "B0007"),
            "evidence_validation": validation(),
        },
        {
            "item_id": "HWI-C",
            "batch_id": "HWB-BATCH",
            "business_case_id": "BROKEN",
            "source_id": "x" * 64,
            "result": "FAILED",
            "candidate": None,
            "evidence_validation": None,
        },
    ]
    workbench = FakeWorkbench(items)
    promotion = HardwareR1KnowledgePromotionService(
        HardwareR1KnowledgePromotionStore(tmp_path / "hardware.db"),
        workbench_service=workbench,
        bridge=bridge,
    )

    result = promotion.intake_batch("HWB-BATCH")
    assert result["summary"]["TOTAL"] == 3
    assert result["summary"]["CANDIDATE_INTAKED"] == 2
    assert result["summary"]["SKIPPED"] == 1
    assert result["auto_publish"] is False


class FailOnceBridge:
    def __init__(self, delegate: HardwareR1GoldenKnowledgeBridge):
        self.delegate = delegate
        self.failed = False

    def precheck(self, *args, **kwargs):
        return self.delegate.precheck(*args, **kwargs)

    def intake(self, *args, **kwargs):
        if not self.failed:
            self.failed = True
            from services.hardware_r1_golden_knowledge_bridge import (
                HardwareR1GoldenBridgeError,
            )
            raise HardwareR1GoldenBridgeError("KNOWLEDGE_UNAVAILABLE")
        return self.delegate.intake(*args, **kwargs)


def test_failed_intake_retry_is_item_scoped(tmp_path: Path) -> None:
    (
        _,
        workbench,
        source_store,
        _,
        knowledge_root,
        transport,
        _,
    ) = setup_case(
        tmp_path,
        case_id="A0152",
        item_id="HWI-RETRY",
        batch_id="HWB-RETRY",
    )
    adapter = HardwareCaseKnowledgeAdapter(
        transport,
        knowledge_release_version=RELEASE,
    )
    real = HardwareR1GoldenKnowledgeBridge(adapter, source_store)
    promotion = HardwareR1KnowledgePromotionService(
        HardwareR1KnowledgePromotionStore(tmp_path / "promotion.db"),
        workbench_service=workbench,
        bridge=FailOnceBridge(real),  # type: ignore[arg-type]
    )

    first = promotion.intake_item("HWI-RETRY")
    assert first["status"] == "INTAKE_FAILED"
    assert first["error_code"] == "KNOWLEDGE_UNAVAILABLE"

    retried = promotion.retry_failed_item("HWI-RETRY")
    assert retried["status"] == "CANDIDATE_INTAKED"
    assert retried["retry_count"] == 1
