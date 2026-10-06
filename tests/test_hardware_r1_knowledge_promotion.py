from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from knowledge_production import KnowledgeReleaseService, create_knowledge_api_app
from repositories import JsonArtifactRepository
from services.hardware_asset_repository import (
    CandidateAssetRepository,
    CandidateAssetRepositoryError,
)
from services.hardware_asset_operation_journal import HardwareAssetOperationJournalError
from services.hardware_case_knowledge_adapter import (
    HardwareCaseKnowledgeAdapter,
    HardwareKnowledgeAdapterError,
)
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
from services.hardware_r1_e2e_nonprod_knowledge import ManagedNonProdReleaseController
from quality_knowledge.web.hardware_r1_workbench_api import (
    create_hardware_r1_workbench_router,
)
from quality_knowledge.web.p0_app import create_p0_app
import services.hardware_case_r1_workbench as workbench_module
from services.hardware_case_r1_workbench import HardwareR1WorkbenchStore


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


def persist_candidate(repository: CandidateAssetRepository, source: Mapping[str, Any], obj: Mapping[str, Any]) -> str:
    saved = repository.create_or_commit_candidate(
        business_case_id=str(obj["identity"]["business_case_id"]),
        source_id=str(source["source_id"]),
        source_ref=str(source["source_ref"]),
        knowledge_object=obj,
        generation_run_id="promotion-test-run",
        pipeline_version="test-pipeline-v1",
        agent_config_version="test-agent-v1",
        knowledge_schema_version="hardware-case-knowledge-object/v1",
        validator_version="test-validator-v1",
    )
    return str(saved["candidate_id"])


def setup_case(
    tmp_path: Path,
    *,
    case_id: str,
    item_id: str,
    batch_id: str,
):
    payload = f"source-{case_id}".encode()
    asset_repository = CandidateAssetRepository(tmp_path / "hardware_asset.db")
    asset_repository.initialize()
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
    candidate_id = persist_candidate(asset_repository, source, candidate)
    item = {
        "item_id": item_id,
        "batch_id": batch_id,
        "business_case_id": case_id,
        "source_id": source["source_id"],
        "candidate_id": candidate_id,
        "result": "CANDIDATE_READY",
        "candidate": candidate,
        "evidence_validation": validation(),
    }
    workbench = FakeWorkbench([item])
    promotion = HardwareR1KnowledgePromotionService(
        HardwareR1KnowledgePromotionStore(tmp_path / "hardware.db"),
        workbench_service=workbench,
        bridge=bridge,
        candidate_repository=asset_repository,
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
    assert first["knowledge_candidate_id"] == "HC-KNOWLEDGE-A0152-R1"
    assert first["asset_candidate_id"].startswith("HCAND-")
    assert first["evidence_refs"]
    durable = promotion.assets.get_candidate(first["asset_candidate_id"])
    ledger = promotion.assets.get_promotion_record(first["asset_candidate_id"])
    assert durable["promotion_status"] == ledger["promotion_status"] == "CANDIDATE_INTAKED"
    assert durable["row_version"] == 3
    assert ledger["knowledge_candidate_id"] == "HC-KNOWLEDGE-A0152-R1"
    assert ledger["origin_batch_id"] == "HWB-1"
    assert ledger["origin_item_id"] == "HWI-A0152"
    assert ledger["retry_count"] == 0
    assert promotion.assets.get_promotion_record(first["asset_candidate_id"])["knowledge_candidate_id"] == first["knowledge_candidate_id"]
    with sqlite3.connect(promotion.assets.db_path) as connection:
        event_types = [row[0] for row in connection.execute(
            "SELECT event_type FROM hardware_candidate_event WHERE candidate_id=? ORDER BY created_at,event_id",
            (first["asset_candidate_id"],),
        )]
    assert "PROMOTION_STARTED" in event_types
    # A fresh service instance recovers solely from the Asset Plane ledger.
    restarted = HardwareR1KnowledgePromotionService(
        HardwareR1KnowledgePromotionStore(promotion.store.db_path, read_only=True),
        workbench_service=promotion.workbench,
        bridge=promotion.bridge,
        candidate_repository=promotion.assets,
    )
    assert restarted.get_item("HWI-A0152")["knowledge_candidate_id"] == first["knowledge_candidate_id"]
    assert not any(call["path"] == "/v1/knowledge/publish" for call in transport.calls)
    assert candidate["review"]["object_status"] == "CANDIDATE"


def test_promotion_reads_durable_candidate_and_locks_it_after_precheck(tmp_path: Path) -> None:
    promotion, workbench, _, _, _, _, golden_object = setup_case(
        tmp_path, case_id="A0152", item_id="HWI-DURABLE", batch_id="HWB-DURABLE"
    )
    item = workbench.items["HWI-DURABLE"]
    item["candidate"] = {"wrong": "Workbench snapshot must not be used"}
    check = promotion.precheck_item("HWI-DURABLE")
    assert check["precheck"]["status"] == "PASS"
    asset = promotion.assets.get_candidate(item["candidate_id"])
    assert asset["knowledge_object"] == golden_object
    assert asset["promotion_status"] == "PRECHECK_PASS"
    modified = copy.deepcopy(golden_object)
    modified["identity"]["raw_title"] = "attempted mutation after precheck"
    with pytest.raises(CandidateAssetRepositoryError, match="CANDIDATE_LOCKED_BY_PROMOTION"):
        promotion.assets.create_or_commit_candidate(
            business_case_id=asset["business_case_id"],
            source_id=asset["source_id"],
            source_ref=asset["source_ref"],
            knowledge_object=modified,
            generation_run_id="second-run",
            pipeline_version="test-pipeline-v1",
            agent_config_version="test-agent-v1",
            knowledge_schema_version="hardware-case-knowledge-object/v1",
            validator_version="test-validator-v1",
        )


def test_promotion_rejects_production_review_required_candidate(tmp_path: Path) -> None:
    promotion, workbench, *_ = setup_case(
        tmp_path, case_id="A0152", item_id="HWI-REVIEW-LOCK", batch_id="HWB-LOCK"
    )
    item = workbench.items["HWI-REVIEW-LOCK"]
    asset = promotion.assets.get_candidate(item["candidate_id"])
    obj = copy.deepcopy(asset["knowledge_object"])
    obj["conflicts"] = [{"conflict_id": "conflict-1", "status": "OPEN", "resolution_status": "NEEDS_REVIEW"}]
    reviewed = promotion.assets.create_or_commit_candidate(
        business_case_id=asset["business_case_id"],
        source_id=asset["source_id"],
        source_ref=asset["source_ref"],
        knowledge_object=obj,
        generation_run_id="review-required-run",
        pipeline_version="test-pipeline-v1",
        agent_config_version="test-agent-v1",
        knowledge_schema_version="hardware-case-knowledge-object/v1",
        validator_version="test-validator-v1",
    )
    item["candidate_id"] = reviewed["candidate_id"]
    with pytest.raises(HardwareR1PromotionError, match="CANDIDATE_LOCKED_BY_REVIEW"):
        promotion.precheck_item("HWI-REVIEW-LOCK")


def test_legacy_promotion_row_migrates_idempotently_to_asset_ledger(tmp_path: Path) -> None:
    promotion, workbench, *_ = setup_case(
        tmp_path, case_id="A0152", item_id="HWI-LEGACY", batch_id="HWB-LEGACY"
    )
    item = workbench.items["HWI-LEGACY"]
    asset = promotion.assets.get_candidate(item["candidate_id"])
    golden_hash = hashlib.sha256(
        json.dumps(asset["knowledge_object"], ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    legacy_writer = HardwareR1KnowledgePromotionStore(
        promotion.store.db_path, read_only=False
    )
    legacy_writer.ensure(
        item_id="HWI-LEGACY", batch_id="HWB-LEGACY", business_case_id="A0152",
        source_id=asset["source_id"], golden_hash=golden_hash,
    )
    legacy_writer.update(
        "HWI-LEGACY", status="PRECHECK_PASS", last_action="PRECHECK",
        candidate_id="KC-LEGACY", review_status="CONFIRMED",
        knowledge_id="K-LEGACY", public_ref="P-LEGACY",
    )
    with sqlite3.connect(promotion.store.db_path) as connection:
        connection.execute(
            "UPDATE hardware_r1_knowledge_promotion SET retry_count=2 WHERE item_id=?",
            ("HWI-LEGACY",),
        )
    migration = promotion.migrate_legacy_records()
    result = promotion.precheck_item("HWI-LEGACY")["promotion"]
    ledger = promotion.assets.get_promotion_record(asset["candidate_id"])
    assert result["asset_candidate_id"] == asset["candidate_id"]
    assert result["knowledge_candidate_id"] == "KC-LEGACY"
    assert ledger["promotion_status"] == "PRECHECK_PASS"
    assert ledger["knowledge_id"] == "K-LEGACY"
    assert ledger["public_ref"] == "P-LEGACY"
    assert ledger["formal_review_status"] == "CONFIRMED"
    assert ledger["retry_count"] == 2
    assert ledger["origin_batch_id"] == "HWB-LEGACY"
    assert ledger["origin_item_id"] == "HWI-LEGACY"
    assert migration == {"status": "PASS", "migrated": 1, "idempotent_reuse": 0}
    # Reading/migrating leaves the old ledger unchanged.
    assert promotion.store.get("HWI-LEGACY")["candidate_id"] == "KC-LEGACY"
    legacy_read_only = HardwareR1KnowledgePromotionStore(
        promotion.store.db_path, read_only=True
    )
    with pytest.raises(HardwareR1PromotionError, match="LEGACY_PROMOTION_READ_ONLY"):
        legacy_read_only.update(
            "HWI-LEGACY", status="VERIFIED", last_action="UNAUTHORIZED"
        )


def test_formal_review_uses_exact_durable_candidate_content(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (
        promotion,
        _,
        _,
        _,
        _,
        transport,
        _,
    ) = setup_case(
        tmp_path,
        case_id="A0206",
        item_id="HWI-A0206",
        batch_id="HWB-DURABLE-ONLY",
    )
    promotion.intake_item("HWI-A0206")
    durable = promotion.assets.get_candidate(
        promotion.workbench.items["HWI-A0206"]["candidate_id"]
    )
    expected = copy.deepcopy(durable["knowledge_object"])
    observed = {}
    original = transport.request

    def capture_review(method, path, *, json_body=None, query=None):
        if method == "POST" and path == "/v1/knowledge/reviews":
            observed["confirmed_value"] = copy.deepcopy(
                (json_body or {}).get("confirmed_value")
            )
        return original(method, path, json_body=json_body, query=query)

    monkeypatch.setattr(transport, "request", capture_review)
    reviewed = promotion.review_item(
        "HWI-A0206",
        reviewer="durable-only-reviewer",
        review_time=NOW,
        review_comment="approval only",
    )
    assert reviewed["status"] == "REVIEW_CONFIRMED"
    assert observed["confirmed_value"] == expected


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
    reviewed = promotion.review_item(
        "HWI-A0207",
        reviewer="hardware-reviewer",
        review_time=NOW,
        review_comment="R1 formal promotion review",
    )
    assert reviewed["status"] == "REVIEW_CONFIRMED"
    durable_review = promotion.assets.get_promotion_record(intake["asset_candidate_id"])
    assert durable_review["formal_review_status"] == "CONFIRMED"

    published = promotion.publish_item(
        "HWI-A0207",
        publisher="hardware-publisher",
        published_at=NOW,
    )
    assert published["status"] == "PUBLISHED_PENDING_QUERY_BACK"
    assert published["knowledge_id"]
    assert published["evidence_refs"] == intake["evidence_refs"]
    durable_publish = promotion.assets.get_promotion_record(intake["asset_candidate_id"])
    assert durable_publish["knowledge_candidate_id"] == intake["knowledge_candidate_id"]
    assert durable_publish["knowledge_id"] == published["knowledge_id"]
    assert durable_publish["public_ref"] == intake["knowledge_candidate_id"]

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
    assert promotion.assets.get_promotion_record(intake["asset_candidate_id"])["promotion_status"] == "VERIFIED"

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
    asset_repository = CandidateAssetRepository(tmp_path / "hardware_asset.db")
    asset_repository.initialize()
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
    for item in items[:2]:
        source = source_a if item["business_case_id"] == "A0152" else source_b
        item["candidate_id"] = persist_candidate(asset_repository, source, item["candidate"])
    workbench = FakeWorkbench(items)
    promotion = HardwareR1KnowledgePromotionService(
        HardwareR1KnowledgePromotionStore(tmp_path / "hardware.db"),
        workbench_service=workbench,
        bridge=bridge,
        candidate_repository=asset_repository,
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
        candidate_repository=CandidateAssetRepository(tmp_path / "hardware_asset.db"),
    )

    first = promotion.intake_item("HWI-RETRY")
    assert first["status"] == "INTAKE_FAILED"
    assert first["error_code"] == "KNOWLEDGE_UNAVAILABLE"

    retried = promotion.retry_failed_item("HWI-RETRY")
    assert retried["status"] == "CANDIDATE_INTAKED"
    assert retried["retry_count"] == 1


class SimulatedPowerLoss(BaseException):
    pass


def _prepare_reviewed_promotion(promotion, candidate):
    intake = promotion.intake_item("HWI-RECOVERY")
    promotion.review_item(
        "HWI-RECOVERY",
        reviewer="recovery-reviewer",
        review_time=NOW,
        review_comment="recovery test",
    )
    return intake


def _count_calls(transport, method, path):
    return sum(call["method"] == method and call["path"] == path for call in transport.calls)


def test_remote_write_is_journaled_before_send_and_remote_key_is_idempotent(tmp_path, monkeypatch):
    promotion, _, _, _, _, transport, _ = setup_case(
        tmp_path, case_id="A0152", item_id="HWI-RECOVERY", batch_id="HWB-RECOVERY"
    )
    original = transport.request
    observed = []

    def inspect_before_send(method, path, *, json_body=None, query=None):
        if method == "POST" and path in {
            "/v1/knowledge/evidences", "/v1/knowledge/candidates",
            "/v1/knowledge/reviews", "/v1/knowledge/publish",
        }:
            expected_type = {
                "/v1/knowledge/evidences": "EVIDENCE_INTAKE",
                "/v1/knowledge/candidates": "CANDIDATE_INTAKE",
                "/v1/knowledge/reviews": "FORMAL_REVIEW",
                "/v1/knowledge/publish": "PUBLISH",
            }[path]
            pending = promotion.operation_journal.list_nonterminal()
            observed.append(
                any(
                    item["candidate_id"] == promotion.workbench.items["HWI-RECOVERY"]["candidate_id"]
                    and item["operation_state"] == "REMOTE_SENT"
                    and item["operation_type"] == expected_type
                    for item in pending
                )
            )
        return original(method, path, json_body=json_body, query=query)

    monkeypatch.setattr(transport, "request", inspect_before_send)
    result = promotion.intake_item("HWI-RECOVERY")
    assert result["status"] == "CANDIDATE_INTAKED"

    promotion.review_item(
        "HWI-RECOVERY", reviewer="journal-reviewer",
        review_time=NOW,
    )
    promotion.publish_item(
        "HWI-RECOVERY", publisher="journal-publisher", published_at=NOW
    )
    assert observed and all(observed)

    candidate_id = result["asset_candidate_id"]
    journal = promotion.operation_journal
    args = {
        "operation_id": "stable-key-check",
        "operation_type": "PUBLISH",
        "business_case_id": "A0152",
        "candidate_id": candidate_id,
        "source_id": result["source_id"],
        "desired_action": "PUBLISH",
        "request_fingerprint": {"candidate_id": "KC-1", "revision": 1},
        "remote_idempotency_key": "KC-1:publish:r1",
    }
    first = journal.prepare(**args)
    second = journal.prepare(**args)
    assert first["remote_idempotency_key"] == second["remote_idempotency_key"]
    with pytest.raises(HardwareAssetOperationJournalError, match="OPERATION_IDEMPOTENCY_CONFLICT"):
        journal.prepare(**{**args, "remote_idempotency_key": "different-key"})


def test_candidate_intake_timeout_reconciles_with_one_controlled_replay(tmp_path, monkeypatch):
    promotion, _, _, _, knowledge_root, transport, candidate = setup_case(
        tmp_path, case_id="A0152", item_id="HWI-RECOVERY", batch_id="HWB-RECOVERY"
    )
    original = transport.request

    def success_then_timeout(method, path, *, json_body=None, query=None):
        response = original(method, path, json_body=json_body, query=query)
        if method == "POST" and path == "/v1/knowledge/candidates" and not getattr(success_then_timeout, "failed", False):
            success_then_timeout.failed = True
            raise HardwareKnowledgeAdapterError("KNOWLEDGE_UNAVAILABLE")
        return response

    monkeypatch.setattr(transport, "request", success_then_timeout)
    with pytest.raises(HardwareR1PromotionError, match="CANDIDATE_INTAKE_RECONCILIATION_REQUIRED"):
        promotion.intake_item("HWI-RECOVERY")
    with pytest.raises(HardwareR1PromotionError, match="CANDIDATE_INTAKE_RECONCILIATION_REQUIRED"):
        promotion.retry_failed_item("HWI-RECOVERY")

    candidate_ops = promotion.operation_journal.list_operations(
        candidate_id=promotion.workbench.items["HWI-RECOVERY"]["candidate_id"],
        operation_type="CANDIDATE_INTAKE",
    )
    assert len(candidate_ops) == 1
    assert candidate_ops[0]["operation_state"] == "OUTCOME_UNKNOWN"
    assert _count_calls(transport, "POST", "/v1/knowledge/candidates") == 1

    recovered = promotion.reconcile_item("HWI-RECOVERY")
    assert recovered["status"] == "CANDIDATE_INTAKED"
    assert recovered["knowledge_candidate_id"] == "HC-KNOWLEDGE-A0152-R1"
    assert _count_calls(transport, "POST", "/v1/knowledge/candidates") == 2
    assert (knowledge_root / "knowledge/production/candidates/HC-KNOWLEDGE-A0152-R1.json").is_file()
    assert candidate["identity"]["business_case_id"] == "A0152"


def test_candidate_reconciliation_replay_timeout_is_never_replayed_again(
    tmp_path, monkeypatch
):
    promotion, _, _, _, _, transport, _ = setup_case(
        tmp_path, case_id="A0152", item_id="HWI-RECOVERY", batch_id="HWB-RECOVERY"
    )
    original = transport.request

    def initial_success_then_timeout(method, path, *, json_body=None, query=None):
        response = original(method, path, json_body=json_body, query=query)
        if method == "POST" and path == "/v1/knowledge/candidates":
            raise HardwareKnowledgeAdapterError("KNOWLEDGE_UNAVAILABLE")
        return response

    monkeypatch.setattr(transport, "request", initial_success_then_timeout)
    with pytest.raises(HardwareR1PromotionError, match="CANDIDATE_INTAKE_RECONCILIATION_REQUIRED"):
        promotion.intake_item("HWI-RECOVERY")

    def replay_response_mismatch(method, path, *, json_body=None, query=None):
        status, response = original(method, path, json_body=json_body, query=query)
        if method == "POST" and path == "/v1/knowledge/candidates":
            response["candidate_id"] = "HC-KNOWLEDGE-WRONG-R1"
        return status, response

    monkeypatch.setattr(transport, "request", replay_response_mismatch)
    with pytest.raises(HardwareR1PromotionError, match="CANDIDATE_INTAKE_RECONCILIATION_REQUIRED"):
        promotion.reconcile_item("HWI-RECOVERY")

    operation = promotion.operation_journal.list_operations(
        candidate_id=promotion.workbench.items["HWI-RECOVERY"]["candidate_id"],
        operation_type="CANDIDATE_INTAKE",
    )[0]
    assert operation["operation_state"] == "OUTCOME_UNKNOWN"
    assert operation["recovery_action"] == "CANDIDATE_INTAKE_CONTROLLED_REPLAY_OUTCOME_UNKNOWN"
    assert _count_calls(transport, "POST", "/v1/knowledge/candidates") == 2
    with pytest.raises(HardwareR1PromotionError, match="CANDIDATE_INTAKE_RECONCILIATION_REQUIRED"):
        promotion.reconcile_item("HWI-RECOVERY")
    assert _count_calls(transport, "POST", "/v1/knowledge/candidates") == 2


def test_evidence_reconciliation_replay_timeout_is_never_replayed_again(
    tmp_path, monkeypatch
):
    promotion, _, _, _, _, _, _ = setup_case(
        tmp_path, case_id="A0152", item_id="HWI-RECOVERY", batch_id="HWB-RECOVERY"
    )
    asset_id = promotion.workbench.items["HWI-RECOVERY"]["candidate_id"]
    asset = promotion.assets.get_candidate(asset_id)
    evidence_id = asset["evidence_refs"][0]["evidence_id"]
    operation = {
        "operation_type": "EVIDENCE_INTAKE",
        "asset_candidate_id": asset_id,
        "business_case_id": "A0152",
        "source_id": asset["source_id"],
        "evidence_id": evidence_id,
        "remote_idempotency_key": evidence_id,
        "request_fingerprint": {
            "asset_candidate_id": asset_id,
            "business_case_id": "A0152",
            "source_id": asset["source_id"],
            "source_ref": asset["source_ref"],
            "evidence_id": evidence_id,
            "locator": {"paragraph": 4, "block_id": "B0004"},
            "excerpt_sha256": hashlib.sha256(b"synthetic evidence").hexdigest(),
            "revision": 1,
        },
    }
    action_calls = 0

    def missing_evidence(_evidence_id):
        raise HardwareKnowledgeAdapterError("EVIDENCE_NOT_FOUND", status_code=404)

    def timeout_after_replay():
        nonlocal action_calls
        action_calls += 1
        raise HardwareKnowledgeAdapterError("KNOWLEDGE_UNAVAILABLE")

    monkeypatch.setattr(promotion.bridge.adapter, "resolve_evidence", missing_evidence)
    with pytest.raises(HardwareR1PromotionError, match="EVIDENCE_INTAKE_RECONCILIATION_REQUIRED"):
        promotion._run_remote_operation(**operation, reconciliation=False, action=timeout_after_replay)
    with pytest.raises(HardwareR1PromotionError, match="EVIDENCE_INTAKE_RECONCILIATION_REQUIRED"):
        promotion._run_remote_operation(**operation, reconciliation=True, action=timeout_after_replay)

    journal_id = promotion._operation_id(asset_id, "EVIDENCE_INTAKE", 1, evidence_id)
    journal = promotion.operation_journal.get(journal_id)
    assert journal["operation_state"] == "OUTCOME_UNKNOWN"
    assert journal["recovery_action"] == "EVIDENCE_INTAKE_CONTROLLED_REPLAY_OUTCOME_UNKNOWN"
    with pytest.raises(HardwareR1PromotionError, match="EVIDENCE_INTAKE_RECONCILIATION_REQUIRED"):
        promotion._run_remote_operation(**operation, reconciliation=True, action=timeout_after_replay)
    assert action_calls == 2


def test_startup_remote_reconciliation_obeys_finite_query_budget(tmp_path, monkeypatch):
    promotion, _, _, _, _, _, _ = setup_case(
        tmp_path, case_id="A0152", item_id="HWI-RECOVERY", batch_id="HWB-RECOVERY"
    )
    asset_id = promotion.workbench.items["HWI-RECOVERY"]["candidate_id"]
    asset = promotion.assets.get_candidate(asset_id)
    for index in range(3):
        evidence_id = f"HCE-UNKNOWN-{index}"
        operation_id = promotion._operation_id(
            asset_id, "EVIDENCE_INTAKE", 1, evidence_id
        )
        promotion.operation_journal.prepare(
            operation_id=operation_id,
            operation_type="EVIDENCE_INTAKE",
            business_case_id="A0152",
            candidate_id=asset_id,
            source_id=asset["source_id"],
            desired_action="HARDWARE_R1_EVIDENCE_INTAKE",
            request_fingerprint={"evidence_id": evidence_id},
            remote_idempotency_key=evidence_id,
        )
        promotion.operation_journal.transition(operation_id, "REMOTE_SENT")
        promotion.operation_journal.transition(
            operation_id, "OUTCOME_UNKNOWN", error_code="REMOTE_OUTCOME_UNKNOWN"
        )

    queried = []

    def unavailable(evidence_id):
        queried.append(evidence_id)
        raise HardwareKnowledgeAdapterError("KNOWLEDGE_UNAVAILABLE", status_code=503)

    monkeypatch.setattr(promotion.bridge.adapter, "resolve_evidence", unavailable)
    result = promotion.reconcile_startup(max_remote_queries=2)

    assert result["startup_queries_used"] == 2
    assert len(queried) == 2
    assert result["pending_remote_reconciliation_count"] == 3
    assert result["blocked_asset_count"] == 1
    assert result["recovery_status"] == "DEGRADED"


def test_promotion_recovery_diagnostics_route_is_maintainer_only(tmp_path):
    promotion, workbench, *_ = setup_case(
        tmp_path, case_id="A0152", item_id="HWI-RECOVERY", batch_id="HWB-RECOVERY"
    )

    app = FastAPI()
    app.include_router(
        create_hardware_r1_workbench_router(
            workbench,
            promotion_service=promotion,
        )
    )
    client = TestClient(app)
    path = "/api/v2/hardware-cases/r1/workbench/promotion/recovery"

    assert client.get(path).status_code == 403
    response = client.get(path, headers={"X-Hardware-Case-Role": "MAINTAINER"})
    assert response.status_code == 200
    assert response.json() == {
        "pending_remote_reconciliation_count": 0,
        "blocked_asset_count": 0,
        "last_recovery_error": None,
        "recovery_status": "COMPLETED",
    }


def test_evidence_remote_success_timeout_resolves_before_candidate_intake(tmp_path, monkeypatch):
    promotion, _, source_store, _, knowledge_root, transport, _ = setup_case(
        tmp_path, case_id="A0152", item_id="HWI-RECOVERY", batch_id="HWB-RECOVERY"
    )
    original = transport.request

    def evidence_success_then_timeout(method, path, *, json_body=None, query=None):
        response = original(method, path, json_body=json_body, query=query)
        if method == "POST" and path == "/v1/knowledge/evidences" and not getattr(evidence_success_then_timeout, "failed", False):
            evidence_success_then_timeout.failed = True
            raise HardwareKnowledgeAdapterError("KNOWLEDGE_UNAVAILABLE")
        return response

    monkeypatch.setattr(transport, "request", evidence_success_then_timeout)
    with pytest.raises(HardwareR1PromotionError, match="EVIDENCE_INTAKE_RECONCILIATION_REQUIRED"):
        promotion.intake_item("HWI-RECOVERY")
    # The public evidence lookup is release-scoped. Seed an unrelated active
    # publication so the recovery path can use only the frozen public query.
    other_source = source_store.register_active_bytes("A0207", "A0207.docx", b"release seed")
    other_golden = golden("A0207", other_source["source_id"], "B0007")
    adapter = promotion.bridge.adapter
    other_evidence = other_golden["evidence"][0]
    other_evidence_id = promotion.bridge.evidence_id(
        "A0207", other_source["source_id"], other_evidence["block_id"]
    )
    adapter.intake_evidence(
        case_id="A0207",
        source_metadata=other_source,
        evidence={
            "evidence_id": other_evidence_id,
            "source_ref": other_source["source_ref"],
            "evidence_type": "PARAGRAPH",
            "locator": {**other_evidence["source_locator"], "block_id": other_evidence["block_id"]},
            "excerpt_or_caption": other_evidence["text"],
        },
        revision=1,
        source_revision=other_source["source_id"],
    )
    adapter.intake_candidate(
        case_id="A0207",
        source_document_id=other_source["source_id"],
        source_ref=other_source["source_ref"],
        structured_content=other_golden,
        evidence_refs=[other_evidence_id],
        revision=1,
        source_version=other_source["source_id"],
    )
    adapter.review_candidate(
        candidate_id="HC-KNOWLEDGE-A0207-R1",
        state="CONFIRMED",
        reviewer="release-seed-reviewer",
        review_time=NOW,
        confirmed_content=other_golden,
        revision=1,
    )
    adapter.publish(
        candidate_id="HC-KNOWLEDGE-A0207-R1",
        hardware_publish_gate={"passed": True},
        evidence_refs=[other_evidence_id],
        publisher="release-seed-publisher",
        published_at=NOW,
        revision=1,
    )
    KnowledgeReleaseService(JsonArtifactRepository(knowledge_root)).build(
        RELEASE, created_at=NOW
    )
    recovered = promotion.reconcile_item("HWI-RECOVERY")
    assert recovered["status"] == "CANDIDATE_INTAKED"
    # One seed evidence plus the timed-out request and its single controlled replay.
    assert _count_calls(transport, "POST", "/v1/knowledge/evidences") == 3
    evidence_id = promotion.assets.get_candidate(
        promotion.workbench.items["HWI-RECOVERY"]["candidate_id"]
    )["evidence_refs"][0]["evidence_id"]
    assert _count_calls(transport, "GET", "/v1/knowledge/evidences/" + evidence_id) == 1
    assert _count_calls(transport, "POST", "/v1/knowledge/candidates") == 2
    assert promotion.recovery_diagnostics()["pending_remote_reconciliation_count"] == 0


def test_formal_review_unknown_is_asset_scoped_and_blocks_publish(tmp_path, monkeypatch):
    promotion, _, _, _, _, transport, candidate = setup_case(
        tmp_path, case_id="A0152", item_id="HWI-RECOVERY", batch_id="HWB-RECOVERY"
    )
    intake = promotion.intake_item("HWI-RECOVERY")
    confirmed = copy.deepcopy(candidate)
    confirmed["reusable_knowledge"]["engineering_rule"]["value"] = "human-confirmed recovery rule"
    original = transport.request

    def review_success_then_timeout(method, path, *, json_body=None, query=None):
        response = original(method, path, json_body=json_body, query=query)
        if method == "POST" and path == "/v1/knowledge/reviews":
            raise HardwareKnowledgeAdapterError("KNOWLEDGE_UNAVAILABLE")
        return response

    monkeypatch.setattr(transport, "request", review_success_then_timeout)
    with pytest.raises(HardwareR1PromotionError, match="FORMAL_REVIEW_RECONCILIATION_REQUIRED"):
        promotion.review_item(
            "HWI-RECOVERY", reviewer="recovery-reviewer",
            review_time=NOW, review_comment="unknown review outcome",
        )
    with pytest.raises(HardwareR1PromotionError, match="FORMAL_REVIEW_RECONCILIATION_REQUIRED"):
        promotion.publish_item("HWI-RECOVERY", publisher="recovery-publisher", published_at=NOW)
    with pytest.raises(HardwareR1PromotionError, match="FORMAL_REVIEW_RECONCILIATION_REQUIRED"):
        promotion.reconcile_item("HWI-RECOVERY")
    assert promotion.get_item("HWI-RECOVERY")["status"] == "CANDIDATE_INTAKED"
    assert promotion.recovery_diagnostics()["blocked_asset_count"] == 1
    assert _count_calls(transport, "POST", "/v1/knowledge/reviews") == 1
    assert _count_calls(transport, "POST", "/v1/knowledge/publish") == 0
    assert intake["asset_candidate_id"] == promotion.workbench.items["HWI-RECOVERY"]["candidate_id"]


@pytest.mark.parametrize("crash_point", ["remote-before-ledger", "ledger-before-source-ref", "source-ref-before-journal"])
def test_publish_remote_success_local_crash_recovers_consistently(tmp_path, monkeypatch, crash_point):
    promotion, _, source_store, source, knowledge_root, transport, candidate = setup_case(
        tmp_path, case_id="A0152", item_id="HWI-RECOVERY", batch_id="HWB-RECOVERY"
    )
    intake = _prepare_reviewed_promotion(promotion, candidate)
    original_transition = promotion._transition
    original_add_reference = promotion.bridge.add_source_reference
    original_journal_transition = promotion._journal_transition
    controller = ManagedNonProdReleaseController(
        JsonArtifactRepository(knowledge_root),
        promotion.bridge.adapter,
        release_prefix=RELEASE,
    )
    assert controller.status()["release_version"] is None
    publish_operation_id = promotion._operation_id(
        intake["asset_candidate_id"], "PUBLISH", 1
    )

    def crash_on_asset_commit(*args, **kwargs):
        if crash_point == "remote-before-ledger" and args[3] == "PUBLISHED_PENDING_QUERY_BACK":
            raise SimulatedPowerLoss()
        return original_transition(*args, **kwargs)

    def crash_before_source_ref(*args, **kwargs):
        if crash_point == "ledger-before-source-ref":
            raise SimulatedPowerLoss()
        return original_add_reference(*args, **kwargs)

    def crash_before_journal_complete(operation_id, state, **kwargs):
        if crash_point == "source-ref-before-journal" and operation_id == publish_operation_id and state == "COMPLETED":
            raise SimulatedPowerLoss()
        return original_journal_transition(operation_id, state, **kwargs)

    monkeypatch.setattr(promotion, "_transition", crash_on_asset_commit)
    monkeypatch.setattr(promotion.bridge, "add_source_reference", crash_before_source_ref)
    monkeypatch.setattr(promotion, "_journal_transition", crash_before_journal_complete)
    with pytest.raises(SimulatedPowerLoss):
        promotion.publish_item("HWI-RECOVERY", publisher="recovery-publisher", published_at=NOW)

    monkeypatch.undo()
    restarted = HardwareR1KnowledgePromotionService(
        HardwareR1KnowledgePromotionStore(promotion.store.db_path, read_only=True),
        workbench_service=promotion.workbench,
        bridge=promotion.bridge,
        candidate_repository=promotion.assets,
        operation_journal=promotion.operation_journal,
    )
    pending = restarted.get_item("HWI-RECOVERY")
    assert pending["reconciliation_required"] is True
    assert pending["reconciliation_operation_type"] == "PUBLISH"
    assert pending["reconciliation_error_code"] == "PUBLISH_RECONCILIATION_REQUIRED"

    startup = restarted.reconcile_startup(
        prepare_publication_query=controller.ensure_queryable_release
    )
    refreshed_release_version = str(controller.status()["release_version"])
    assert refreshed_release_version.startswith(RELEASE + "-R")
    repaired = restarted.get_item("HWI-RECOVERY")
    assert startup["startup_queries_used"] == 1
    assert repaired["status"] == "PUBLISHED_PENDING_QUERY_BACK"
    assert repaired["knowledge_id"]
    settled = restarted.get_item("HWI-RECOVERY")
    assert settled["reconciliation_required"] is False
    assert settled["reconciliation_operation_type"] is None
    assert settled["reconciliation_error_code"] is None
    journal = promotion.operation_journal.get(publish_operation_id)
    assert journal["operation_state"] == "COMPLETED"
    refs = source_store.formal_knowledge_references("A0152")
    assert any(item["knowledge_id"] == repaired["knowledge_id"] for item in refs)
    assert _count_calls(transport, "POST", "/v1/knowledge/publish") == 1
    assert restarted.recovery_diagnostics()["pending_remote_reconciliation_count"] == 0
    assert source["source_id"] == promotion.assets.get_candidate(intake["asset_candidate_id"])["source_id"]


def test_pending_publish_reconciliation_is_exposed_by_api_and_reconcile_does_not_republish(
    tmp_path, monkeypatch
):
    promotion, workbench, _, _, knowledge_root, transport, candidate = setup_case(
        tmp_path, case_id="A0152", item_id="HWI-RECOVERY", batch_id="HWB-RECOVERY"
    )
    intake = _prepare_reviewed_promotion(promotion, candidate)
    original_transition = promotion._transition
    controller = ManagedNonProdReleaseController(
        JsonArtifactRepository(knowledge_root),
        promotion.bridge.adapter,
        release_prefix=RELEASE,
    )
    assert controller.status()["release_version"] is None

    def crash_after_remote_publish(item, asset, record, target, **kwargs):
        if target == "PUBLISHED_PENDING_QUERY_BACK":
            raise SimulatedPowerLoss()
        return original_transition(item, asset, record, target, **kwargs)

    monkeypatch.setattr(promotion, "_transition", crash_after_remote_publish)
    with pytest.raises(SimulatedPowerLoss):
        promotion.publish_item(
            "HWI-RECOVERY", publisher="reconciliation-publisher", published_at=NOW
        )
    publish_operation = promotion.operation_journal.get(
        promotion._operation_id(intake["asset_candidate_id"], "PUBLISH", 1)
    )
    assert publish_operation["operation_state"] in {
        "REMOTE_SENT",
        "OUTCOME_UNKNOWN",
        "RECONCILING",
    }
    assert _count_calls(transport, "POST", "/v1/knowledge/publish") == 1

    app = FastAPI()
    app.include_router(
        create_hardware_r1_workbench_router(
            workbench,
            promotion_service=promotion,
            release_controller=controller,
        )
    )
    client = TestClient(app)
    headers = {"X-Hardware-Case-Role": "MAINTAINER"}
    promotion_path = (
        "/api/v2/hardware-cases/r1/workbench/items/HWI-RECOVERY/promotion"
    )
    response = client.get(promotion_path, headers=headers)
    assert response.status_code == 200, response.text
    assert {
        key: response.json().get(key)
        for key in (
            "reconciliation_required",
            "reconciliation_operation_type",
            "reconciliation_error_code",
        )
    } == {
        "reconciliation_required": True,
        "reconciliation_operation_type": "PUBLISH",
        "reconciliation_error_code": "PUBLISH_RECONCILIATION_REQUIRED",
    }

    monkeypatch.undo()
    reconcile = client.post(promotion_path + "/reconcile", headers=headers)
    assert reconcile.status_code == 200, reconcile.text
    refreshed_release_version = str(controller.status()["release_version"])
    assert refreshed_release_version.startswith(RELEASE + "-R")
    assert reconcile.json()["status"] == "PUBLISHED_PENDING_QUERY_BACK"
    assert reconcile.json()["reconciled"] is True
    assert _count_calls(transport, "POST", "/v1/knowledge/publish") == 1


def test_p0_app_starts_and_reconciles_pending_publish_without_republishing(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        workbench_module, "uuid4", lambda: SimpleNamespace(hex="b" * 32)
    )
    workbench_store = HardwareR1WorkbenchStore(tmp_path / "workbench.db")
    batch_id = workbench_store.create_batch()
    item_id = "HWI-" + "a" * 16
    promotion, _, _, source, knowledge_root, transport, candidate = setup_case(
        tmp_path, case_id="A0152", item_id=item_id, batch_id=batch_id
    )
    intake = promotion.intake_item(item_id)
    promotion.review_item(
        item_id,
        reviewer="startup-recovery-reviewer",
        review_time=NOW,
        review_comment="startup recovery test",
    )
    monkeypatch.setattr(workbench_module, "uuid4", lambda: SimpleNamespace(hex="a" * 32))
    stored_item_id = workbench_store.add_item(
        batch_id,
        source_file="A0152-synthetic.docx",
        business_case_id="A0152",
        source_id=source["source_id"],
        orchestration_status="CANDIDATE_READY",
        snapshot={"source": {"source_id": source["source_id"]}},
        result={
            "pipeline_status": "GOLDEN_PREVIEW_READY",
            "status": "PASS",
            "evidence_validation": validation(),
            "knowledge_object": candidate,
            "provider_call_count": 0,
        },
        candidate_id=intake["asset_candidate_id"],
    )
    assert stored_item_id == item_id

    original_transition = promotion._transition

    def crash_after_remote_publish(item, asset, record, target, **kwargs):
        if target == "PUBLISHED_PENDING_QUERY_BACK":
            KnowledgeReleaseService(JsonArtifactRepository(knowledge_root)).build(
                RELEASE, created_at=NOW
            )
            raise SimulatedPowerLoss()
        return original_transition(item, asset, record, target, **kwargs)

    monkeypatch.setattr(promotion, "_transition", crash_after_remote_publish)
    with pytest.raises(SimulatedPowerLoss):
        promotion.publish_item(
            item_id, publisher="startup-recovery-publisher", published_at=NOW
        )
    assert _count_calls(transport, "POST", "/v1/knowledge/publish") == 1
    monkeypatch.undo()

    app = create_p0_app(
        tmp_path / "hardware.db",
        hardware_case_db_path=tmp_path / "hardware.db",
        hardware_case_source_root=tmp_path / "sources",
        hardware_r1_workbench_db_path=tmp_path / "workbench.db",
        hardware_knowledge_adapter=promotion.bridge.adapter,
        hardware_knowledge_release_version=RELEASE,
        hardware_startup_status={"status": "READY", "ready": True},
        enabled_domains={"HARDWARE_CASE"},
    )
    assert app.state.hardware_r1_promotion_status["ready"] is True
    recovery = app.state.hardware_r1_promotion_status["remote_recovery"]
    assert recovery["pending_remote_reconciliation_count"] == 0
    assert recovery["startup_queries_used"] == 1
    assert promotion.operation_journal.get(
        promotion._operation_id(intake["asset_candidate_id"], "PUBLISH", 1)
    )["operation_state"] == "COMPLETED"
    assert _count_calls(transport, "POST", "/v1/knowledge/publish") == 1

    with TestClient(app) as client:
        ready = client.get("/ready")
        assert ready.status_code in {200, 503}, ready.text
        assert ready.json()["service"] == "HARDWARE_CASE"


def test_candidate_remote_success_crash_before_promotion_ledger_recovers_on_restart(tmp_path, monkeypatch):
    promotion, _, _, _, _, transport, _ = setup_case(
        tmp_path, case_id="A0152", item_id="HWI-RECOVERY", batch_id="HWB-RECOVERY"
    )
    original_transition = promotion._transition

    def crash_before_local_candidate_commit(item, asset, record, target, **kwargs):
        if target == "CANDIDATE_INTAKED":
            raise SimulatedPowerLoss()
        return original_transition(item, asset, record, target, **kwargs)

    monkeypatch.setattr(promotion, "_transition", crash_before_local_candidate_commit)
    with pytest.raises(SimulatedPowerLoss):
        promotion.intake_item("HWI-RECOVERY")
    assert _count_calls(transport, "POST", "/v1/knowledge/candidates") == 1
    monkeypatch.undo()
    restarted = HardwareR1KnowledgePromotionService(
        HardwareR1KnowledgePromotionStore(promotion.store.db_path, read_only=True),
        workbench_service=promotion.workbench,
        bridge=promotion.bridge,
        candidate_repository=promotion.assets,
        operation_journal=promotion.operation_journal,
    )
    recovered = restarted.reconcile_item("HWI-RECOVERY")
    assert recovered["status"] == "CANDIDATE_INTAKED"
    assert _count_calls(transport, "POST", "/v1/knowledge/candidates") == 1
