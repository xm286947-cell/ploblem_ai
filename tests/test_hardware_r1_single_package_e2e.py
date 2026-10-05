from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

from fastapi import FastAPI
from fastapi.testclient import TestClient

from quality_knowledge.web.hardware_knowledge_consumption_api import (
    create_hardware_knowledge_consumption_router,
)
from quality_knowledge.web.hardware_r1_e2e_api import create_hardware_r1_e2e_router
from quality_knowledge.web.hardware_r1_workbench_api import create_hardware_r1_workbench_router
from quality_knowledge.web.p0_app import create_p0_app
from services.hardware_knowledge_consumption import (
    HardwareKnowledgeConsumptionProjectionStore,
    HardwareKnowledgeConsumptionService,
)
from services.hardware_data_root import HardwareDataRootResolver
from services.hardware_startup_coordinator import HardwareStartupCoordinator
from scripts.hardware_case_web_start import ROOT as PRODUCT_ROOT
from quality_knowledge.web.p0_app import create_p0_app
import services.hardware_case_r1_workbench as workbench_module


MAINTAINER = {"X-Hardware-Case-Role": "MAINTAINER"}


class _SourceStore:
    def get_active_source(self, case_id: str):
        return {"business_case_id": case_id, "source_id": "SRC-1", "source_ref": "source://original.docx"}

    def preview(self, source_ref, locator, *, context_blocks):
        assert source_ref == "source://original.docx"
        assert locator == {"paragraph": 1}
        return {"source_status": "AVAILABLE", "preview_status": "AVAILABLE", "blocks": [{"text": "Synthetic evidence passage", "matched": True}]}

    def get_metadata(self, source_ref):
        return {"mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "display_name": "synthetic.docx"}

    def resolve_path(self, source_ref):
        return Path(__file__)


class _KnowledgeAdapter:
    def resolve_evidence(self, evidence_id):
        return {"evidence_id": evidence_id, "source": {"source_id": "SRC-1", "uri": "source://original.docx", "metadata": {"hardware_locator": {"paragraph": 1}}}}


def test_e2e_readiness_is_read_only_and_reports_nonprod_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("HARDWARE_R1_E2E_KNOWLEDGE_ENV", "NON_PROD")
    monkeypatch.setenv("HARDWARE_KNOWLEDGE_BASE_URL", "https://nonprod.example")
    monkeypatch.setenv("HARDWARE_KNOWLEDGE_RELEASE_VERSION", "test-release")
    app = FastAPI()
    app.include_router(create_hardware_r1_e2e_router(
        app_root=tmp_path / "app", data_root=tmp_path / "isolated", normal_data_root=tmp_path / "normal",
        promotion_status={"ready": True, "code": "READY"},
    ))
    state = TestClient(app).get("/api/e2e/hardware-r1/readiness").json()
    assert state["data_root_isolated"] is True
    assert state["knowledge_environment"] == "NON_PROD"
    assert state["provider_call_performed"] is False
    assert state["auto_publish"] is False
    assert "api_key" not in str(state).lower()


def test_e2e_publish_fails_closed_without_nonprod_configuration():
    app = FastAPI()
    app.include_router(create_hardware_r1_workbench_router(object(), promotion_service=object(), publish_allowed=False))
    response = TestClient(app).post(
        "/api/v2/hardware-cases/r1/workbench/items/ITEM-1/promotion/publish",
        headers=MAINTAINER,
        json={"publisher": "tester"},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "BLOCKED_BY_ENVIRONMENT"


def test_e2e_evidence_recall_is_source_identity_bound_and_maintainer_only(tmp_path):
    service = HardwareKnowledgeConsumptionService(HardwareKnowledgeConsumptionProjectionStore(tmp_path / "projection.db"))
    app = FastAPI()
    app.include_router(create_hardware_knowledge_consumption_router(
        service, source_store=_SourceStore(), knowledge_adapter=_KnowledgeAdapter(),
    ))
    client = TestClient(app)
    route = "/api/public/hardware-knowledge/v1/evidence/EV-1/source-preview?business_case_id=CASE-1"
    assert client.get(route).status_code == 403
    response = client.get(route, headers=MAINTAINER)
    assert response.status_code == 200, response.text
    assert response.json()["blocks"][0]["text"] == "Synthetic evidence passage"
    file_response = client.get(route.replace("source-preview", "source-file"), headers=MAINTAINER)
    assert file_response.status_code == 200
    assert file_response.headers["content-disposition"].endswith('filename="synthetic.docx"')


def test_consumption_evidence_route_rejects_source_identity_mismatch(tmp_path):
    class WrongAdapter(_KnowledgeAdapter):
        def resolve_evidence(self, evidence_id):
            result = super().resolve_evidence(evidence_id)
            result["source"]["source_id"] = "ANOTHER-SOURCE"
            return result

    service = HardwareKnowledgeConsumptionService(HardwareKnowledgeConsumptionProjectionStore(tmp_path / "projection.db"))
    app = FastAPI()
    app.include_router(create_hardware_knowledge_consumption_router(service, source_store=_SourceStore(), knowledge_adapter=WrongAdapter()))
    response = TestClient(app).get(
        "/api/public/hardware-knowledge/v1/evidence/EV-1/source-preview?business_case_id=CASE-1", headers=MAINTAINER,
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "EVIDENCE_SOURCE_IDENTITY_MISMATCH"


def test_single_package_pages_are_existing_hardware_case_web(tmp_path, monkeypatch):
    monkeypatch.setenv("HARDWARE_R1_E2E_PROFILE", "1")
    data_root = tmp_path / "isolated-data"
    db = data_root / "db" / "hardware_case_mvp.db"
    app = create_p0_app(
        db,
        hardware_case_db_path=db,
        hardware_case_source_root=data_root / "sources",
        hardware_tree_upload_dir=data_root / "sources" / "tree_uploads",
        hardware_r1_workbench_db_path=data_root / "db" / "workbench.db",
        hardware_r1_preview_db_path=data_root / "rebuildable" / "preview.db",
        enabled_domains={"HARDWARE_CASE"},
    )
    client = TestClient(app)
    assert client.get("/p0/hardware-cases/e2e").status_code == 200
    normal_knowledge = client.get("/p0/hardware-cases/knowledge")
    e2e_knowledge = client.get("/p0/hardware-cases/knowledge?e2e=1")
    assert normal_knowledge.status_code == e2e_knowledge.status_code == 200
    assert "hardware_case_e2e_evidence.js" not in normal_knowledge.text
    assert "hardware_case_e2e_evidence.js?v=wave4-e2e-v2" in e2e_knowledge.text
    assert "hardware_case_knowledge_consumption.js?v=wave4-stage4-v1" in e2e_knowledge.text
    assert client.get("/p0/static/hardware_case_e2e.js").status_code == 200

    consumption_js = client.get("/p0/static/hardware_case_knowledge_consumption.js")
    evidence_js = client.get("/p0/static/hardware_case_e2e_evidence.js")
    assert consumption_js.status_code == evidence_js.status_code == 200
    assert "detail.dataset.businessCaseId = item.business_case_id" in consumption_js.text
    assert "detail.dataset.businessCaseId" in evidence_js.text
    assert ".split('·')" not in evidence_js.text


def _synthetic_docx(path: Path) -> bytes:
    document = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
<w:p><w:r><w:t>MCU UART controller</w:t></w:r></w:p>
<w:p><w:r><w:t>UART output becomes garbled when pull-up drive is insufficient.</w:t></w:r></w:p>
</w:body></w:document>'''
    with ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document)
    return path.read_bytes()


def test_single_package_fake_provider_golden_path_through_search_and_evidence(tmp_path, monkeypatch):
    """Exercise the UI-facing product APIs; the only extraction result is deterministic fake data."""
    monkeypatch.setenv("HARDWARE_R1_E2E_PROFILE", "1")
    monkeypatch.setenv("HARDWARE_R1_E2E_KNOWLEDGE_ENV", "NON_PROD")
    monkeypatch.setenv("HARDWARE_R1_E2E_KNOWLEDGE_MODE", "LOCAL_NON_PROD")
    monkeypatch.delenv("HARDWARE_KNOWLEDGE_BASE_URL", raising=False)
    monkeypatch.setenv("HARDWARE_KNOWLEDGE_RELEASE_VERSION", "SYNTHETIC-E2E")
    calls = {"provider": 0, "fake_pipeline": 0}

    def fake_pipeline(snapshot, _structurer, **_kwargs):
        calls["fake_pipeline"] += 1
        case_id = snapshot["identity"]["business_case_id"]
        source_id = snapshot["source"]["source_id"]
        blocks = snapshot["structure"]["blocks"]
        block = blocks[1] if len(blocks) > 1 else blocks[0]
        block_id = block["block_id"]
        text = block["text"]
        locator = dict(block["source_locator"])
        candidate = {
            "contract_version": "hardware-case-knowledge-object/v1",
            "identity": {"business_case_id": case_id, "raw_title": "MCU UART Synthetic", "identity_status": "PARSED"},
            "source_fact": {
                "source_id": source_id, "business_case_id": case_id, "original_filename": snapshot["source"]["file_name"],
                "markdown_view": {"view_version": "hardware-markdown-view/v1", "markdown": text},
                "snapshot_version": "hardware-document-snapshot/v1",
                "source_locators": [{"block_id": block_id, "source_locator": {**locator, "block_id": block_id}}],
            },
            "engineering_context": {"primary_subject": {"value": "MCU UART controller", "extraction_status": "EXTRACTED", "evidence_block_ids": [block_id]}},
            "observed_problem": {"symptom": {"value": "UART output becomes garbled", "extraction_status": "EXTRACTED", "evidence_block_ids": [block_id]}},
            "engineering_analysis": {"root_cause": {"value": "pull-up drive is insufficient", "extraction_status": "EXTRACTED", "evidence_block_ids": [block_id]}},
            "engineering_resolution": {"actions": {"value": "Review pull-up design", "extraction_status": "EXTRACTED", "evidence_block_ids": [block_id]}},
            "reusable_knowledge": {"engineering_rule": {"value": "Check interface drive margin", "extraction_status": "EXTRACTED", "evidence_block_ids": [block_id], "derived_from_fields": ["facts.root_cause"], "review_status": "UNREVIEWED"}},
            "evidence": [{"block_id": block_id, "block_type": "PARAGRAPH", "text": text, "image_ref": None, "source_locator": {**locator, "block_id": block_id}}],
            "conflicts": [], "review": {"object_status": "CANDIDATE", "reviewer": None, "reviewed_at": None, "field_decisions": []},
            "provenance": {"source_id": source_id, "snapshot_version": "hardware-document-snapshot/v1", "markdown_version": "hardware-markdown-view/v1", "agent_id": "hardware_case.r1_extract", "agent_config_version": "fake-stage-a-b/v1", "runtime_run_id": "fake-run-1", "extraction_contract_version": "hardware-r1-extraction/v2"},
        }
        return {
            "pipeline_version": "hardware-r1-agent-pipeline/v1.3.3", "execution_trace_version": "hardware-r1-execution-trace/v1.5",
            "pipeline_status": "GOLDEN_PREVIEW_READY", "status": "PASS", "failed_stage": None, "error_code": None,
            "provider_call_count": 0, "validation_retry_count": 0, "run_id": "fake-run-1",
            "knowledge_object_contract_version": "hardware-case-knowledge-object/v1",
            "runtime": {"stage_a": {"agent_config_version": "fake-stage-a/v1"}, "stage_b": {"agent_config_version": "fake-stage-b/v1"}},
            "evidence_validation": {"status": "PASS", "fabricated_fact_count": 0, "fabricated_block_id_count": 0},
            "knowledge_object": candidate,
        }

    monkeypatch.setattr(workbench_module, "run_r1_agent_extraction", fake_pipeline)
    data_root = tmp_path / "isolated-e2e"
    monkeypatch.setenv("HARDWARE_DATA_ROOT", str(data_root))
    resolver = HardwareDataRootResolver(
        PRODUCT_ROOT,
        environment={**__import__("os").environ, "HARDWARE_DATA_ROOT": str(data_root)},
        bootstrap_path=tmp_path / "isolated-bootstrap.json",
        legacy_roots=(),
    )
    resolution = resolver.resolve()
    startup_status = HardwareStartupCoordinator(PRODUCT_ROOT, resolver=resolver, resolution=resolution).run()
    assert startup_status["ready"], startup_status
    db = data_root / "db" / "hardware_case_mvp.db"
    app = create_p0_app(
        db,
        hardware_case_db_path=db,
        hardware_case_source_root=data_root / "sources",
        hardware_tree_upload_dir=data_root / "sources" / "tree_uploads",
        hardware_r1_workbench_db_path=data_root / "db" / "workbench.db",
        hardware_r1_preview_db_path=data_root / "rebuildable" / "preview.db",
        hardware_case_r1_structurer=object(),
        hardware_startup_status=startup_status,
        enabled_domains={"HARDWARE_CASE"},
    )
    client = TestClient(app)
    path = tmp_path / "synthetic.docx"
    payload = _synthetic_docx(path)
    upload = client.post("/api/v2/hardware-cases/r1/workbench/batches", headers=MAINTAINER, files={"files": ("A0152-synthetic.docx", payload, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
    assert upload.status_code == 201, upload.text
    batch_id = upload.json()["batch_id"]
    run = client.post(f"/api/v2/hardware-cases/r1/workbench/batches/{batch_id}/run-resume", headers=MAINTAINER)
    assert run.status_code == 200, run.text
    item = run.json()["items"][0]
    assert item["result"] == "CANDIDATE_READY", {key: item.get(key) for key in ("result", "orchestration_status", "failed_stage", "error_code", "source_id", "candidate_id")}
    item_id = item["item_id"]
    assert item["candidate_id"]
    assert item["candidate_asset"]["candidate_id"] == item["candidate_id"]
    assert calls == {"provider": 0, "fake_pipeline": 1}

    for endpoint, body in [
        ("precheck", None),
        ("intake", None),
        ("review", {"reviewer": "synthetic-reviewer", "confirmed_content": item["candidate"], "review_comment": "CI fake review"}),
        ("publish", {"publisher": "synthetic-publisher"}),
        ("verify", None),
    ]:
        response = client.post(f"/api/v2/hardware-cases/r1/workbench/items/{item_id}/promotion/{endpoint}", headers=MAINTAINER, json=body)
        assert response.status_code == 200, f"{endpoint}: {response.status_code} {response.text}"
        if endpoint == "publish":
            release = response.json().get("knowledge_release") or {}
            assert release.get("mode") == "LOCAL_NON_PROD", response.json()
            assert str(release.get("release_version") or "").startswith(
                "SYNTHETIC-E2E-R"
            )
        if endpoint == "verify":
            assert response.json().get("status") == "VERIFIED", {key: response.json().get(key) for key in ("status", "error_code", "knowledge_id", "public_ref")}
    project = client.post(f"/api/v2/hardware-cases/r1/workbench/consumption/project/{item['candidate_id']}", headers=MAINTAINER)
    assert project.status_code == 200, project.text
    search = client.get("/api/public/hardware-knowledge/v1/search", params={"text": "MCU UART"})
    assert search.status_code == 200, search.text
    assert search.json()["results"]
    knowledge_id = search.json()["results"][0]["knowledge_id"]
    detail = client.get(f"/api/public/hardware-knowledge/v1/objects/{knowledge_id}")
    assert detail.status_code == 200, detail.text
    evidence_id = detail.json()["evidence_refs"][0]
    preview = client.get(f"/api/public/hardware-knowledge/v1/evidence/{evidence_id}/source-preview", params={"business_case_id": "A0152"}, headers=MAINTAINER)
    assert preview.status_code == 200, preview.text
    assert "UART output becomes garbled" in str(preview.json())
    original = client.get(f"/api/public/hardware-knowledge/v1/evidence/{evidence_id}/source-file", params={"business_case_id": "A0152"}, headers=MAINTAINER)
    assert original.status_code == 200
    assert original.content == payload
    assert calls["provider"] == 0
