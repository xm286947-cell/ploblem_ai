from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from io import BytesIO
from xml.sax.saxutils import escape

import pytest


_CODE_ROOT = Path(
    os.environ.get("HARDWARE_DURABILITY_CODE_ROOT")
    or Path(__file__).resolve().parents[1]
).expanduser().resolve()
sys.path.insert(0, str(_CODE_ROOT))

from fastapi.testclient import TestClient
from knowledge_production import KnowledgeReleaseService, create_knowledge_api_app
from repositories import JsonArtifactRepository
from services.hardware_asset_backup import (
    HardwareAssetBackupCoordinator,
    HardwareAssetBackupError,
)
from services.hardware_asset_repository import CandidateAssetRepository
from services.hardware_asset_operation_journal import HardwareAssetOperationJournal
from services.hardware_case_knowledge_adapter import HardwareCaseKnowledgeAdapter
from services.hardware_case_source_store import HardwareCaseSourceStore
from services.hardware_data_root import HardwareDataRootResolver, MANIFEST_RELATIVE_PATH
from services.hardware_durable_mutation_gate import HardwareApplicationLock
from services.hardware_r1_knowledge_promotion import HardwareR1KnowledgePromotionService
from services.hardware_startup_coordinator import (
    HardwareStartupCoordinator,
    ROOT_RECOVERY_MARKER,
)
from quality_knowledge.web import create_p0_app


RELEASE = "HARDWARE-R1-DURABILITY-TEST"
ROLE = {"X-Hardware-Case-Role": "MAINTAINER"}
MIME_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
NOW = datetime(2026, 10, 3, 6, 0, tzinfo=timezone.utc)
WORKBENCH = "/api/v2/hardware-cases/r1/workbench"

CASE_SPECS = {
    "A0152": {"title_subject": "CPU", "machine_subject": "MCU", "run": True},
    "A0156": {"title_subject": "CPU", "machine_subject": "MCU", "run": True},
    "A0162": {"title_subject": "MCU", "machine_subject": "MCU", "run": True},
    "A0207": {"title_subject": "ADC", "machine_subject": "ADC", "run": True},
    "A0210": {"title_subject": "MCU", "machine_subject": "MCU", "run": False},
}


class CountingTransport:
    """A deterministic in-process implementation of the Unified Knowledge API."""

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
        self.calls.append({"method": method, "path": path})
        response = self.client.request(
            method,
            path,
            json=dict(json_body) if json_body is not None else None,
            params=dict(query) if query is not None else None,
        )
        payload = response.json()
        if not isinstance(payload, dict):
            raise AssertionError("Unified Knowledge public API returned a non-object")
        return response.status_code, payload


class DeterministicPipeline:
    """Schema-valid Stage A/B test adapter; it never calls an AI provider."""

    def __init__(self) -> None:
        self.calls = 0

    @staticmethod
    def _field(value: Any = None, refs: list[str] | None = None) -> dict[str, Any]:
        return {
            "value": value,
            "status": "EXTRACTED" if value is not None else "MISSING",
            "evidence_block_ids": list(refs or []),
        }

    def run_stage_a(self, payload: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        source = payload.get("source_fact") or {}
        case_id = str(source.get("business_case_id") or "")
        title = str(source.get("raw_title") or "")
        evidence_index = payload.get("evidence_index") or []
        if not evidence_index:
            return {"ok": False, "error_code": "TEST_EVIDENCE_MISSING"}
        block_id = str(evidence_index[0]["block_id"])
        if case_id == "A0207" or title.startswith("ADC"):
            text = "ADC参考源精度偏差导致模拟量输出偏差；调整参考源后校准复测通过。"
            subject, symptom, cause, action, verified = (
                "ADC", "模拟量输出偏差", "参考源精度偏差", "调整参考源", "校准复测通过"
            )
            analysis = "ADC参考源精度偏差"
        else:
            text = "MCU连接串口屏时TX默认弱上拉导致带载电平下降；改用推挽输出后长期可靠性测试未再复现。"
            subject, symptom, cause, action, verified = (
                "MCU", "带载电平下降", "TX默认弱上拉", "改用推挽输出", "长期可靠性测试未再复现"
            )
            analysis = "TX默认弱上拉导致带载电平下降"
        refs = [block_id]
        facts = {
            name: self._field()
            for name in (
                "background", "symptom", "impact", "occurrence_condition",
                "analysis_process", "failure_mode", "root_cause",
                "failure_mechanism", "actions", "verification_result", "conclusion",
            )
        }
        facts.update(
            {
                "symptom": self._field(symptom, refs),
                "analysis_process": self._field(analysis, refs),
                "root_cause": self._field(cause, refs),
                "actions": self._field(action, refs),
                "verification_result": self._field(verified, refs),
            }
        )
        data = {
            "engineering_context": {
                "primary_subject": self._field(subject, refs),
                "component_or_device": self._field(subject, refs),
                "interface": self._field("UART" if subject == "MCU" else "模拟量", refs),
                "signal": self._field("TX" if subject == "MCU" else "参考电压", refs),
                "peer_device_or_load": self._field("串口屏" if subject == "MCU" else "模拟量输出", refs),
                "key_parameters": [],
            },
            "facts": facts,
        }
        return {
            "ok": True,
            "data": data,
            "runtime": {
                "run_id": f"durability-{case_id}-stage-a",
                "task_id": f"durability-{case_id}-task-a",
                "agent_id": "hardware_case.r1_case_extract",
                "agent_config_version": "durability-test-v1",
                "provider_call_count": 0,
                "observed_provider_call_count": 0,
                "provider_attempts": [],
            },
        }

    def run_stage_b(self, payload: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        context = payload.get("engineering_context") or {}
        case_id = "A0207" if (context.get("primary_subject") or {}).get("value") == "ADC" else "OTHER"
        refs = [str(item["block_id"]) for item in payload.get("evidence_blocks") or []]
        reusable = {
            name: {
                "value": None,
                "status": "MISSING",
                "derived_from_fields": [],
                "evidence_block_ids": [],
            }
            for name in (
                "engineering_rule", "design_constraint", "diagnostic_clue",
                "verification_method", "applicability", "conclusion",
            )
        }
        if case_id == "A0207":
            rule = "ADC参考源精度偏差导致模拟量输出偏差"
            clue = "模拟量输出偏差"
            method = "校准复测通过"
            rule_sources = ["root_cause"]
            clue_sources = ["symptom"]
            method_sources = ["verification_result"]
        else:
            rule = "TX默认弱上拉导致带载电平下降"
            clue = "带载电平下降"
            method = "长期可靠性测试未再复现"
            rule_sources = ["root_cause"]
            clue_sources = ["symptom"]
            method_sources = ["verification_result"]
        for name, value, origins in (
            ("engineering_rule", rule, rule_sources),
            ("diagnostic_clue", clue, clue_sources),
            ("verification_method", method, method_sources),
        ):
            reusable[name] = {
                "value": value,
                "status": "EXTRACTED",
                "derived_from_fields": origins,
                "evidence_block_ids": refs,
            }
        return {
            "ok": True,
            "data": {"reusable_knowledge_candidate": reusable},
            "runtime": {
                "run_id": f"durability-{case_id}-stage-b",
                "task_id": f"durability-{case_id}-task-b",
                "agent_id": "hardware_case.r1_reuse_derive",
                "agent_config_version": "durability-test-v1",
                "provider_call_count": 0,
                "observed_provider_call_count": 0,
                "provider_attempts": [],
            },
        }


def _docx_bytes(text: str) -> bytes:
    stream = BytesIO()
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body><w:p><w:r><w:t xml:space="preserve">'
        + escape(text)
        + '</w:t></w:r></w:p><w:sectPr/></w:body></w:document>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '</Types>'
    )
    relationships = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        '</Relationships>'
    )
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", relationships)
        archive.writestr("word/document.xml", document_xml)
    return stream.getvalue()


def _root_context(data_root: Path, application_root: Path) -> tuple[HardwareDataRootResolver, Path]:
    bootstrap_path = data_root.parent / "user-config" / f"{data_root.name}-bootstrap.json"
    resolver = HardwareDataRootResolver(
        application_root,
        environment={"HARDWARE_DATA_ROOT": str(data_root)},
        bootstrap_path=bootstrap_path,
        legacy_roots=(),
    )
    return resolver, bootstrap_path


def _startup(application_root: Path, resolver: HardwareDataRootResolver) -> dict[str, Any]:
    resolution = resolver.resolve()
    status = HardwareStartupCoordinator(
        application_root,
        resolver=resolver,
        resolution=resolution,
    ).run()
    assert status.get("ready") is True, status
    assert status.get("data_root") == str(resolver.resolve().data_root)
    return status


def _build_app(
    data_root: Path,
    application_root: Path,
    startup_status: dict[str, Any],
    *,
    structurer: Any | None = None,
    knowledge_adapter: Any | None = None,
):
    hardware_db = data_root / "db" / "hardware_case_mvp.db"
    return create_p0_app(
        data_root / "db" / "quality-test.db",
        stage_runner=object(),
        project_root=application_root,
        hardware_case_db_path=hardware_db,
        hardware_tree_upload_dir=data_root / "sources" / "tree_uploads",
        hardware_case_source_root=data_root / "sources",
        hardware_r1_workbench_db_path=data_root / "db" / "workbench_runtime.db",
        hardware_r1_preview_db_path=data_root / "rebuildable" / "preview.db",
        hardware_startup_status=startup_status,
        hardware_case_r1_structurer=structurer,
        hardware_knowledge_adapter=knowledge_adapter,
        hardware_knowledge_release_version=RELEASE,
        portrait_db_path=":memory:",
        enabled_domains={"HARDWARE_CASE"},
    )


def _active_state(
    app: Any,
    data_root: Path,
    startup_status: Mapping[str, Any],
    *,
    backup_id: str | None,
) -> dict[str, Any]:
    sources = app.state.hardware_case_source_store
    assets = app.state.hardware_candidate_asset_repository
    workbench = app.state.hardware_r1_workbench_service
    journal = app.state.hardware_asset_operation_journal
    source_rows: dict[str, dict[str, Any]] = {}
    candidate_rows: dict[str, dict[str, Any]] = {}
    promotion_rows: dict[str, dict[str, Any]] = {}
    formal_refs: list[dict[str, Any]] = []
    batch_rows: list[dict[str, Any]] = []

    for case_id in CASE_SPECS:
        try:
            source = sources.get_active_source(case_id)
        except Exception as error:
            if str(getattr(error, "code", "")) != "SOURCE_NOT_REGISTERED":
                raise
            source = None
        if source:
            source_rows[case_id] = {
                "business_case_id": case_id,
                "binding_status": source.get("binding_status"),
                "source_id": source.get("source_id"),
                "source_ref": source.get("source_ref"),
                "sha256": source.get("sha256"),
                "size_bytes": source.get("size_bytes"),
                "source_status": source.get("source_status"),
                "display_name": source.get("display_name"),
            }
            formal_refs.extend(sources.formal_knowledge_references(case_id))
            candidates = assets.find_by_source_id(str(source["source_id"]))
            candidate = next(
                (item for item in candidates if item.get("business_case_id") == case_id),
                None,
            )
            if candidate:
                knowledge = candidate.get("knowledge_object") or {}
                review = knowledge.get("review") or {}
                candidate_rows[case_id] = {
                    "candidate_id": candidate.get("candidate_id"),
                    "business_case_id": candidate.get("business_case_id"),
                    "source_id": candidate.get("source_id"),
                    "source_ref": candidate.get("source_ref"),
                    "candidate_hash": candidate.get("candidate_hash"),
                    "row_version": candidate.get("row_version"),
                    "asset_status": candidate.get("asset_status"),
                    "production_review_status": candidate.get("production_review_status"),
                    "promotion_status": candidate.get("promotion_status"),
                    "review": {
                        "object_status": review.get("object_status"),
                        "reviewer": review.get("reviewer"),
                        "reviewed_at": review.get("reviewed_at"),
                        "field_decisions": review.get("field_decisions") or [],
                        "open_conflicts": [
                            {
                                "conflict_id": item.get("conflict_id"),
                                "field": item.get("field"),
                                "status": item.get("status"),
                                "resolution_status": item.get("resolution_status"),
                            }
                            for item in knowledge.get("conflicts") or []
                        ],
                    },
                    "evidence_ids": sorted(
                        str(item.get("evidence_id"))
                        for item in candidate.get("evidence_refs") or []
                    ),
                    "evidence_refs": candidate.get("evidence_refs") or [],
                }
                promotion = assets.get_promotion_record(str(candidate["candidate_id"]))
                if promotion:
                    promotion_rows[case_id] = {
                        key: promotion.get(key)
                        for key in (
                            "asset_candidate_id", "promotion_status", "knowledge_candidate_id",
                            "knowledge_id", "public_ref", "formal_review_status", "origin_batch_id",
                            "origin_item_id", "retry_count", "published_at", "verified_at",
                        )
                    }
                    promotion_rows[case_id]["evidence_ids"] = candidate_rows[case_id]["evidence_ids"]

    for batch in workbench.store.list_batches(limit=200):
        response = workbench.get_batch(str(batch["batch_id"]))
        batch_rows.append(
            {
                "batch_id": response["batch_id"],
                "item_count": len(response.get("items") or []),
                "items": [
                    {
                        "item_id": item.get("item_id"),
                        "business_case_id": item.get("business_case_id"),
                        "source_id": item.get("source_id"),
                        "candidate_id": item.get("candidate_id"),
                        "result": item.get("result"),
                        "run_ref": item.get("run_ref"),
                    }
                    for item in response.get("items") or []
                ],
            }
        )

    operations = journal.list_operations()
    manifest_path = data_root / MANIFEST_RELATIVE_PATH
    data_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    state = {
        "contract_version": "hardware-patch-durability-state/v1",
        "installation_id": data_manifest.get("installation_id"),
        "persistent_data_root": str(data_root.resolve()),
        "data_layout_version": data_manifest.get("data_layout_version"),
        "hardware_schema_version": data_manifest.get("hardware_schema_version"),
        "asset_schema_version": data_manifest.get("asset_schema_version"),
        "workbench_schema_version": data_manifest.get("workbench_schema_version"),
        "startup": {
            "status": startup_status.get("status"),
            "ready": startup_status.get("ready"),
            "installation_id": startup_status.get("installation_id"),
            "migration_id": startup_status.get("migration_id"),
            "last_migration_id": data_manifest.get("last_migration_id"),
        },
        "source_count": len(source_rows),
        "sources": source_rows,
        "candidate_count": len(candidate_rows),
        "candidates": candidate_rows,
        "reviews": {
            case_id: value["review"]
            for case_id, value in candidate_rows.items()
            if value["production_review_status"] in {"REQUIRED", "RESOLVED"}
        },
        "promotions": promotion_rows,
        "operation_journal": {
            "total": len(operations),
            "nonterminal": [item for item in operations if item.get("operation_state") in {
                "PREPARED", "LOCAL_COMMITTED", "REMOTE_SENT", "OUTCOME_UNKNOWN", "RECONCILING"
            }],
            "terminal": [item for item in operations if item.get("operation_state") in {
                "COMPLETED", "FAILED", "CANCELLED"
            }],
        },
        "formal_knowledge_refs": sorted(
            formal_refs,
            key=lambda item: (str(item.get("business_case_id")), str(item.get("knowledge_id"))),
        ),
        "batch_count": len(batch_rows),
        "item_count": sum(item["item_count"] for item in batch_rows),
        "batches": sorted(batch_rows, key=lambda item: str(item["batch_id"])),
        "backup_id": backup_id,
    }
    return state


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _read_config() -> tuple[Path, Path, Path]:
    evidence_root = Path(os.environ["HARDWARE_DURABILITY_EVIDENCE_ROOT"]).resolve()
    data_root = Path(os.environ["HARDWARE_DURABILITY_DATA_ROOT"]).resolve()
    application_root = Path(os.environ["HARDWARE_DURABILITY_ACTIVE_APP_ROOT"]).resolve()
    return evidence_root, data_root, application_root


def _upload_case(client: TestClient, case_id: str) -> tuple[str, str]:
    spec = CASE_SPECS[case_id]
    name = f"{case_id}-{spec['title_subject']}-durability.docx"
    body = (
        "ADC参考源精度偏差导致模拟量输出偏差；调整参考源后校准复测通过。"
        if case_id == "A0207"
        else "MCU连接串口屏时TX默认弱上拉导致带载电平下降；改用推挽输出后长期可靠性测试未再复现。"
    )
    response = client.post(
        f"{WORKBENCH}/batches",
        headers=ROLE,
        files=[("files", (name, _docx_bytes(body), MIME_DOCX))],
    )
    assert response.status_code == 201, response.text
    batch = response.json()
    assert len(batch.get("items") or []) == 1, batch
    return str(batch["batch_id"]), str(batch["items"][0]["item_id"])


def _run_case(client: TestClient, batch_id: str) -> dict[str, Any]:
    response = client.post(f"{WORKBENCH}/batches/{batch_id}/run-resume", headers=ROLE)
    assert response.status_code == 200, response.text
    items = response.json().get("items") or []
    assert len(items) == 1, response.json()
    return items[0]


def _assert_http(response: Any, expected: int = 200) -> dict[str, Any]:
    assert response.status_code == expected, response.text
    result = response.json()
    assert isinstance(result, dict)
    return result


def _prepare_old_install(evidence_root: Path, data_root: Path, app_root: Path) -> dict[str, Any]:
    resolver, _bootstrap = _root_context(data_root, app_root)
    status = _startup(app_root, resolver)
    assert resolver.resolve().classification == "EXISTING_INSTALL"

    knowledge_root = evidence_root.parent / "deterministic-unified-knowledge"
    knowledge_app = create_knowledge_api_app(
        str(knowledge_root),
        knowledge_release_version=RELEASE,
        service_id="hardware_patch_durability_test",
    )
    pipeline = DeterministicPipeline()
    state_transition_witnesses: dict[str, dict[str, Any]] = {}
    transport: CountingTransport
    with TestClient(knowledge_app) as knowledge_client:
        transport = CountingTransport(knowledge_client)
        adapter = HardwareCaseKnowledgeAdapter(
            transport,
            knowledge_release_version=RELEASE,
        )
        app = _build_app(
            data_root,
            app_root,
            status,
            structurer=pipeline,
            knowledge_adapter=adapter,
        )
        with TestClient(app) as client:
            for case_id in CASE_SPECS:
                batch_id, item_id = _upload_case(client, case_id)
                if not CASE_SPECS[case_id]["run"]:
                    continue
                item = _run_case(client, batch_id)
                assert item["item_id"] == item_id
                assert item["result"] in {"CANDIDATE_READY", "REVIEW"}, item
                if case_id == "A0152":
                    assert item["result"] == "REVIEW"
                    assert item["candidate_asset"]["production_review_status"] == "REQUIRED"
                elif case_id == "A0156":
                    assert item["result"] == "REVIEW"
                    conflict = next(
                        value for value in item["candidate"]["conflicts"]
                        if value.get("resolution_status") == "NEEDS_REVIEW"
                    )
                    reviewed = _assert_http(
                        client.post(
                            f"{WORKBENCH}/items/{item_id}/review-conflicts/{conflict['conflict_id']}/resolve",
                            headers=ROLE,
                            json={"decision_source": "SOURCE_RAW_TITLE", "reviewer": "durability-reviewer"},
                        )
                    )
                    assert reviewed["result"] == "CANDIDATE_READY"
                    assert reviewed["candidate_asset"]["production_review_status"] == "RESOLVED"
                elif case_id == "A0162":
                    assert item["result"] == "CANDIDATE_READY"
                    _assert_http(client.post(f"{WORKBENCH}/items/{item_id}/promotion/precheck", headers=ROLE))
                    intake = _assert_http(client.post(f"{WORKBENCH}/items/{item_id}/promotion/intake", headers=ROLE))
                    assert intake["status"] == "CANDIDATE_INTAKED"
                elif case_id == "A0207":
                    assert item["result"] == "CANDIDATE_READY"
                    _assert_http(client.post(f"{WORKBENCH}/items/{item_id}/promotion/precheck", headers=ROLE))
                    intake = _assert_http(client.post(f"{WORKBENCH}/items/{item_id}/promotion/intake", headers=ROLE))
                    assert intake["status"] == "CANDIDATE_INTAKED"
                    current = _assert_http(client.get(f"{WORKBENCH}/items/{item_id}", headers=ROLE))
                    reviewed = _assert_http(
                        client.post(
                            f"{WORKBENCH}/items/{item_id}/promotion/review",
                            headers=ROLE,
                            json={
                                "reviewer": "durability-formal-reviewer",
                                "confirmed_content": current["candidate"],
                                "review_comment": "Deterministic patch durability scenario",
                            },
                        )
                    )
                    assert reviewed["status"] == "REVIEW_CONFIRMED"
                    published = _assert_http(
                        client.post(
                            f"{WORKBENCH}/items/{item_id}/promotion/publish",
                            headers=ROLE,
                            json={"publisher": "durability-publisher"},
                        )
                    )
                    assert published["status"] == "PUBLISHED_PENDING_QUERY_BACK"
                    KnowledgeReleaseService(JsonArtifactRepository(knowledge_root)).build(
                        RELEASE,
                        created_at=NOW,
                    )
                    published_state = _active_state(app, data_root, status, backup_id=None)
                    published_record = published_state["promotions"]["A0207"]
                    formal_refs = [
                        ref for ref in published_state["formal_knowledge_refs"]
                        if ref.get("business_case_id") == "A0207"
                    ]
                    assert published_record["promotion_status"] == "PUBLISHED_PENDING_QUERY_BACK", published_record
                    assert published_record["knowledge_id"] and published_record["public_ref"], published_record
                    assert formal_refs and formal_refs[0].get("knowledge_id") == published_record["knowledge_id"], formal_refs
                    state_transition_witnesses["S6_FORMAL_PUBLISHED"] = {
                        "promotion_status": published_record["promotion_status"],
                        "knowledge_candidate_id": published_record["knowledge_candidate_id"],
                        "knowledge_id": published_record["knowledge_id"],
                        "public_ref": published_record["public_ref"],
                        "formal_source_references": formal_refs,
                        "evidence_ids": published_record["evidence_ids"],
                    }
                    verified = _assert_http(
                        client.post(f"{WORKBENCH}/items/{item_id}/promotion/verify", headers=ROLE)
                    )
                    assert verified["status"] == "VERIFIED"
                    verified_state = _active_state(app, data_root, status, backup_id=None)
                    verified_record = verified_state["promotions"]["A0207"]
                    assert verified_record["promotion_status"] == "VERIFIED", verified_record
                    assert verified["knowledge_id"] == verified_record["knowledge_id"], verified
                    assert verified["public_ref"] == verified_record["public_ref"], verified
                    assert verified["evidence_refs"] == intake["evidence_refs"], verified
                    assert verified_record["evidence_ids"] == published_record["evidence_ids"]
                    assert any(
                        ref.get("knowledge_id") == verified_record["knowledge_id"]
                        and ref.get("business_case_id") == "A0207"
                        for ref in verified_state["formal_knowledge_refs"]
                    )
                    state_transition_witnesses["S7_VERIFIED"] = {
                        "promotion_status": verified_record["promotion_status"],
                        "knowledge_candidate_id": verified_record["knowledge_candidate_id"],
                        "knowledge_id": verified_record["knowledge_id"],
                        "public_ref": verified_record["public_ref"],
                        "evidence_ids": verified_record["evidence_ids"],
                        "query_back_identity_matches_local_ledger": True,
                    }

            assert pipeline.calls == 8, f"expected 4 deterministic two-stage runs, got {pipeline.calls}"
            state = _active_state(app, data_root, status, backup_id=None)
            assert state["source_count"] == 5
            assert state["candidate_count"] == 4
            assert state["batch_count"] == 5 and state["item_count"] == 5
            assert state["promotions"]["A0162"]["promotion_status"] == "CANDIDATE_INTAKED"
            assert state["promotions"]["A0207"]["promotion_status"] == "VERIFIED"

    # A successful lock acquisition proves graceful shutdown and no active app mutation.
    app_lock = HardwareApplicationLock(data_root)
    app_lock.acquire()
    app_lock.release()

    backup = HardwareAssetBackupCoordinator(resolver)
    backup_manifest = backup.create_backup(reason="PRE_UPGRADE")
    assert backup_manifest.get("backup_state") == "PUBLISHED"
    verified_backup = backup.verify_backup(str(backup_manifest["backup_id"]))
    assert verified_backup.get("backup_state") == "PUBLISHED"

    # Refresh after the backup service publishes its own audit and manifest records.
    read_status = dict(status)
    state = _active_state(app, data_root, read_status, backup_id=str(backup_manifest["backup_id"]))
    assert state["installation_id"] == status["installation_id"]
    assert state["startup"]["ready"] is True
    state["state_transition_witnesses"] = state_transition_witnesses
    _write_json(evidence_root / "pre_patch_state.json", state)
    _write_json(
        evidence_root / "backup_verify.json",
        {
            "backup_id": backup_manifest["backup_id"],
            "backup_state": backup_manifest["backup_state"],
            "verify_status": "PASS",
            "source_count": backup_manifest.get("source_count"),
            "candidate_count": backup_manifest.get("candidate_count"),
        },
    )
    _write_json(
        evidence_root / "source_hash_report.json",
        {case_id: {key: value.get(key) for key in ("source_id", "source_ref", "sha256", "size_bytes", "source_status")} for case_id, value in state["sources"].items()},
    )
    _write_json(
        evidence_root / "candidate_hash_report.json",
        {case_id: {key: value.get(key) for key in ("candidate_id", "candidate_hash", "row_version", "evidence_ids")} for case_id, value in state["candidates"].items()},
    )
    _write_json(evidence_root / "review_report.json", state["reviews"])
    _write_json(evidence_root / "promotion_report.json", state["promotions"])
    _write_json(
        evidence_root / "provider_call_report.json",
        {
            "old_state_creation_stage_invocations": pipeline.calls,
            "old_state_creation_provider_calls": 0,
            "stage_runtime_provider_call_count": 0,
            "public_contract_adapter_calls": len(transport.calls),
            "patch_startup_provider_calls": 0,
        },
    )
    _write_json(
        evidence_root / "startup_trace.json",
        {
            "old_startup": status,
            "phase": "OLD_STATE_CREATED_AND_GRACEFULLY_STOPPED",
            "no_active_mutation": True,
        },
    )
    return state


def _critical_view(state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: state.get(key)
        for key in (
            "installation_id", "persistent_data_root", "data_layout_version",
            "hardware_schema_version", "asset_schema_version", "workbench_schema_version",
            "sources", "candidates", "reviews", "promotions", "operation_journal",
            "formal_knowledge_refs", "batch_count", "item_count", "batches",
        )
    }


def _diff(left: Any, right: Any, path: str = "") -> list[dict[str, Any]]:
    if type(left) is not type(right):
        return [{"path": path or "$", "before": left, "after": right}]
    if isinstance(left, dict):
        output: list[dict[str, Any]] = []
        for key in sorted(set(left) | set(right)):
            child = f"{path}.{key}" if path else key
            if key not in left or key not in right:
                output.append({"path": child, "before": left.get(key), "after": right.get(key)})
            else:
                output.extend(_diff(left[key], right[key], child))
        return output
    if isinstance(left, list):
        if len(left) != len(right):
            return [{"path": path, "before": left, "after": right}]
        output = []
        for index, (a, b) in enumerate(zip(left, right)):
            output.extend(_diff(a, b, f"{path}[{index}]"))
        return output
    return [] if left == right else [{"path": path, "before": left, "after": right}]


def _seed_restore_drill(application_root: Path, root: Path) -> tuple[HardwareDataRootResolver, dict[str, Any]]:
    resolver, _ = _root_context(root, application_root)
    status = _startup(application_root, resolver)
    app = _build_app(root, application_root, status, structurer=DeterministicPipeline())
    with TestClient(app) as client:
        batch_id, item_id = _upload_case(client, "A0162")
        item = _run_case(client, batch_id)
        assert item["result"] == "CANDIDATE_READY"
        snapshot = _active_state(app, root, status, backup_id=None)
    app_lock = HardwareApplicationLock(root)
    app_lock.acquire()
    app_lock.release()
    backup = HardwareAssetBackupCoordinator(resolver)
    published = backup.create_backup(reason="PRE_UPGRADE")
    assert backup.verify_backup(str(published["backup_id"]))["backup_state"] == "PUBLISHED"
    snapshot["backup_id"] = published["backup_id"]
    snapshot["restore_item_id"] = item_id
    snapshot["restore_batch_id"] = batch_id
    return resolver, snapshot


def _run_restore_drill(evidence_root: Path, application_root: Path, primary_root: Path) -> dict[str, Any]:
    restore_root = primary_root.parent / (primary_root.name + "-restore-drill")
    assert not restore_root.exists(), f"isolated restore root already exists: {restore_root}"
    resolver, pre_state = _seed_restore_drill(application_root, restore_root)
    backup_id = str(pre_state["backup_id"])

    # Exercise a real deletion through the existing Source service, then restore offline.
    asset_repository = CandidateAssetRepository(restore_root / "db" / "hardware_asset.db")
    source_store = HardwareCaseSourceStore(
        restore_root / "db" / "hardware_case_mvp.db",
        restore_root / "sources",
        initialize_schema=False,
        operation_journal=HardwareAssetOperationJournal(restore_root / "db" / "hardware_asset.db"),
        candidate_repository=asset_repository,
    )
    source_store.delete_active_source("A0162", deleted_by="D1-RESTORE-DRILL")
    coordinator = HardwareAssetBackupCoordinator(resolver)
    startup_trace: list[dict[str, Any]] = []
    original_run = HardwareStartupCoordinator.run

    def trace_run(instance: HardwareStartupCoordinator) -> dict[str, Any]:
        result = original_run(instance)
        startup_trace.append(dict(result))
        return result

    HardwareStartupCoordinator.run = trace_run  # type: ignore[method-assign]
    try:
        restore = coordinator.restore(backup_id)
    except HardwareAssetBackupError as error:
        raise AssertionError(
            f"isolated restore failed: {error.code}; startup_trace={startup_trace}"
        ) from error
    finally:
        HardwareStartupCoordinator.run = original_run  # type: ignore[method-assign]
    assert restore["status"] == "RESTORED" and restore["startup_status"] == "READY"
    status = _startup(application_root, resolver)
    app = _build_app(restore_root, application_root, status)
    with TestClient(app):
        post_state = _active_state(app, restore_root, status, backup_id=backup_id)
    diff = _diff(_critical_view(pre_state), _critical_view(post_state))
    report = {
        "status": "PASS" if not diff else "BLOCKED",
        "isolated_root": str(restore_root),
        "primary_root_touched": False,
        "backup_id": backup_id,
        "restore_id": restore.get("restore_id"),
        "critical_diff_count": len(diff),
        "diff": diff,
    }
    _write_json(evidence_root / "restore_verify.json", report)
    assert not diff, diff
    return report


def _verify_new_install(evidence_root: Path, data_root: Path, app_root: Path) -> dict[str, Any]:
    pre_state = json.loads((evidence_root / "pre_patch_state.json").read_text(encoding="utf-8"))
    resolver, bootstrap_path = _root_context(data_root, app_root)
    before_root = data_root.resolve()
    before_bootstrap = json.loads(bootstrap_path.read_text(encoding="utf-8"))
    assert before_bootstrap.get("installation_id") == pre_state["installation_id"]
    assert before_bootstrap.get("persistent_data_root") == str(before_root)

    first_status = _startup(app_root, resolver)
    assert first_status.get("installation_id") == pre_state["installation_id"]
    assert resolver.resolve().data_root.resolve() == before_root
    durable_db_sizes = {
        name: (data_root / "db" / name).stat().st_size
        for name in ("hardware_case_mvp.db", "hardware_asset.db", "workbench_runtime.db")
    }
    assert all(size > 0 for size in durable_db_sizes.values()), durable_db_sizes
    app = _build_app(data_root, app_root, first_status)
    with TestClient(app) as client:
        readiness = client.get("/api/system/hardware/startup")
        assert readiness.status_code == 200, readiness.text
        live_app = client.app
        initial_state = _active_state(live_app, data_root, first_status, backup_id=pre_state.get("backup_id"))
        # Preview is disposable; clearing it must not touch durable identities.
        preview_rows_cleared = live_app.state.hardware_r1_preview_store.clear()
        preview_path = data_root / "rebuildable" / "preview.db"
        cache_rows_invalidated = 0
        from services.hardware_case_r1_runtime import invalidate_hardware_r1_stage_cache

        for source in initial_state["sources"].values():
            cache_rows_invalidated += invalidate_hardware_r1_stage_cache(
                str(source["source_id"]),
                root=app_root,
                environ={"HARDWARE_CASE_RUNTIME_DB": str(app_root / "data" / "runtime" / "hardware_case_runtime.db")},
            )
        after_clear_state = _active_state(live_app, data_root, first_status, backup_id=pre_state.get("backup_id"))
        assert not _diff(_critical_view(initial_state), _critical_view(after_clear_state))
    # Stop the app before removing its disposable SQLite file. Windows keeps
    # SQLite file handles exclusive until app shutdown; the durable-state
    # isolation assertion above is unchanged.
    if preview_path.exists():
        preview_path.unlink()

    # The same upgraded root must start a second time without a second migration.
    first_manifest = json.loads((data_root / MANIFEST_RELATIVE_PATH).read_text(encoding="utf-8"))
    second_status = _startup(app_root, resolver)
    second_manifest = json.loads((data_root / MANIFEST_RELATIVE_PATH).read_text(encoding="utf-8"))
    assert second_status.get("installation_id") == pre_state["installation_id"]
    assert second_manifest.get("last_migration_id") == first_manifest.get("last_migration_id")
    assert second_manifest.get("hardware_schema_version") == first_manifest.get("hardware_schema_version")
    assert second_manifest.get("asset_schema_version") == first_manifest.get("asset_schema_version")
    assert second_manifest.get("workbench_schema_version") == first_manifest.get("workbench_schema_version")
    app2 = _build_app(data_root, app_root, second_status)
    with TestClient(app2) as client:
        final_state = _active_state(client.app, data_root, second_status, backup_id=pre_state.get("backup_id"))

    diff = _diff(_critical_view(pre_state), _critical_view(final_state))
    _write_json(evidence_root / "post_patch_state.json", final_state)
    _write_json(
        evidence_root / "identity_diff.json",
        {"critical_diff_count": len(diff), "diff": diff, "status": "PASS" if not diff else "BLOCKED"},
    )
    _write_json(
        evidence_root / "startup_trace.json",
        {
            "old_installation_id": pre_state["installation_id"],
            "new_installation_id": final_state["installation_id"],
            "persistent_data_root_before": str(before_root),
            "persistent_data_root_after": str(resolver.resolve().data_root.resolve()),
            "first_startup": first_status,
            "second_startup": second_status,
            "first_last_migration_id": first_manifest.get("last_migration_id"),
            "second_last_migration_id": second_manifest.get("last_migration_id"),
            "no_second_migration": second_manifest.get("last_migration_id") == first_manifest.get("last_migration_id"),
            "durable_db_sizes": durable_db_sizes,
            "no_empty_db_fallback": all(size > 0 for size in durable_db_sizes.values()),
            "preview_rows_cleared": preview_rows_cleared,
            "stage_cache_rows_invalidated": cache_rows_invalidated,
        },
    )
    _write_json(
        evidence_root / "provider_call_report.json",
        {
            "startup_provider_calls": 0,
            "migration_provider_calls": 0,
            "patch_provider_calls": 0,
            "deterministic_test_adapter_only_for_old_business_state": True,
        },
    )
    _write_json(
        evidence_root / "source_hash_report.json",
        {case_id: {key: value.get(key) for key in ("source_id", "source_ref", "sha256", "size_bytes", "source_status")} for case_id, value in final_state["sources"].items()},
    )
    _write_json(
        evidence_root / "candidate_hash_report.json",
        {case_id: {key: value.get(key) for key in ("candidate_id", "candidate_hash", "row_version", "evidence_ids")} for case_id, value in final_state["candidates"].items()},
    )
    _write_json(evidence_root / "review_report.json", final_state["reviews"])
    _write_json(evidence_root / "promotion_report.json", final_state["promotions"])

    witnesses = pre_state.get("state_transition_witnesses") or {}
    published_witness = witnesses.get("S6_FORMAL_PUBLISHED") or {}
    verified_witness = witnesses.get("S7_VERIFIED") or {}
    states = {
        "S1_SOURCE_ONLY": final_state["sources"].get("A0210") is not None and "A0210" not in final_state["candidates"],
        "S2_CANDIDATE_READY": any(item.get("business_case_id") == "A0162" and item.get("candidate_id") and item.get("result") == "CANDIDATE_READY" for batch in final_state["batches"] for item in batch["items"]),
        "S3_PRODUCTION_REVIEW_REQUIRED": (
            final_state["candidates"].get("A0152", {}).get("production_review_status") == "REQUIRED"
            and any(
                item.get("resolution_status") == "NEEDS_REVIEW"
                for item in final_state["candidates"].get("A0152", {}).get("review", {}).get("open_conflicts", [])
            )
        ),
        "S4_PRODUCTION_REVIEW_RESOLVED": final_state["candidates"].get("A0156", {}).get("production_review_status") == "RESOLVED",
        "S5_PROMOTION_IN_PROGRESS": final_state["promotions"].get("A0162", {}).get("promotion_status") == "CANDIDATE_INTAKED",
        "S6_FORMAL_PUBLISHED": (
            published_witness.get("promotion_status") == "PUBLISHED_PENDING_QUERY_BACK"
            and bool(published_witness.get("knowledge_candidate_id"))
            and bool(published_witness.get("knowledge_id"))
            and bool(published_witness.get("public_ref"))
            and bool(published_witness.get("formal_source_references"))
            and bool(published_witness.get("evidence_ids"))
        ),
        "S7_VERIFIED": (
            verified_witness.get("promotion_status") == "VERIFIED"
            and verified_witness.get("query_back_identity_matches_local_ledger") is True
            and verified_witness.get("knowledge_id") == final_state["promotions"].get("A0207", {}).get("knowledge_id")
            and verified_witness.get("public_ref") == final_state["promotions"].get("A0207", {}).get("public_ref")
            and verified_witness.get("evidence_ids") == final_state["promotions"].get("A0207", {}).get("evidence_ids")
        ),
    }
    _write_json(evidence_root / "state_coverage.json", states)
    assert all(states.values()), states
    assert not diff, diff
    assert final_state["installation_id"] == pre_state["installation_id"]
    assert final_state["persistent_data_root"] == pre_state["persistent_data_root"]
    restore_report = _run_restore_drill(evidence_root, app_root, data_root)
    assert restore_report["status"] == "PASS"
    return {"state_coverage": states, "critical_diff_count": len(diff), "restore": restore_report}


def test_f2_backup_staging_crash_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "persistent-data"
    app_root = tmp_path / "application"
    app_root.mkdir()
    resolver, _ = _root_context(root, app_root)
    _startup(app_root, resolver)
    coordinator = HardwareAssetBackupCoordinator(resolver)

    def crash_during_snapshot(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("injected backup staging interruption")

    monkeypatch.setattr(coordinator, "_snapshot_db", crash_during_snapshot)
    with pytest.raises(HardwareAssetBackupError, match="BACKUP_STAGING_FAILED"):
        coordinator.create_backup(reason="PRE_UPGRADE")
    assert coordinator.list_backups() == []
    assert not (root / ROOT_RECOVERY_MARKER).exists()
    staging = list((root / "backups").glob(".creating-*"))
    assert len(staging) == 1
    assert not (staging[0] / "manifest" / "backup_manifest.json").exists()


def test_f4_restore_activation_crash_does_not_activate_partial_restore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "persistent-data"
    app_root = tmp_path / "application"
    app_root.mkdir()
    bootstrap = tmp_path / "user-config" / "bootstrap.json"
    resolver = HardwareDataRootResolver(
        app_root,
        environment={"HARDWARE_DATA_ROOT": str(root)},
        bootstrap_path=bootstrap,
        legacy_roots=(),
    )
    startup = HardwareStartupCoordinator(app_root, resolver=resolver).run()
    assert startup["ready"] is True, startup
    source_store = HardwareCaseSourceStore(
        root / "db" / "hardware_case_mvp.db",
        root / "sources",
        initialize_schema=False,
    )
    source_store.register_active_bytes("A0210", "A0210.docx", b"restore-fault-source")
    coordinator = HardwareAssetBackupCoordinator(resolver)
    backup = coordinator.create_backup(reason="PRE_UPGRADE")

    def crash_before_activation(*_args: Any, **_kwargs: Any) -> None:
        raise HardwareAssetBackupError("RESTORE_ACTIVATION_FAILED")

    monkeypatch.setattr(coordinator, "_activate_restore", crash_before_activation)
    with pytest.raises(HardwareAssetBackupError, match="RESTORE_ACTIVATION_FAILED"):
        coordinator.restore(str(backup["backup_id"]))
    assert (root / ROOT_RECOVERY_MARKER).is_file()
    # The fault is injected before the root swap; the original root remains in
    # place and no backup payload is activated over it.
    assert (root / "db" / "hardware_case_mvp.db").is_file()
    assert source_store.get_active_source("A0210")["source_id"]


def test_patch_durability_phase(tmp_path: Path) -> None:
    phase = os.environ.get("HARDWARE_DURABILITY_PHASE", "selftest").strip().lower()
    if phase in {"prepare", "selftest"}:
        if phase == "selftest":
            evidence_root = tmp_path / "PATCH_DURABILITY_EVIDENCE"
            data_root = tmp_path / "persistent-data"
            app_root = _CODE_ROOT
        else:
            evidence_root, data_root, app_root = _read_config()
        evidence_root.mkdir(parents=True, exist_ok=True)
        _prepare_old_install(evidence_root, data_root, app_root)
        if phase == "selftest":
            _verify_new_install(evidence_root, data_root, app_root)
        return
    if phase == "verify":
        evidence_root, data_root, app_root = _read_config()
        result = _verify_new_install(evidence_root, data_root, app_root)
        assert result["critical_diff_count"] == 0
        return
    raise AssertionError(f"unsupported durability phase: {phase}")
