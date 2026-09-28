from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

from tools.openai_mock.server import Behavior, MockState, Scenario, create_server
from storage_life import ai, core, document_pipeline, runtime_bridge
from storage_life.app import app


class _SequenceMockState(MockState):
    def __init__(self, payloads):
        super().__init__()
        self.payloads = list(payloads)
        self.last_call = 0

    def register_call(self, key, record):
        self.last_call = super().register_call(key, record)
        return self.last_call

    def scenario(self, key):
        payload = self.payloads[min(max(self.last_call - 1, 0), len(self.payloads) - 1)]
        return Scenario(key=key, payload=payload, behavior=Behavior(require_auth=True))


def _identity_response() -> str:
    return json.dumps({
        "vendor": {"value": "GigaDevice", "page": 1,
                   "quote": "GigaDevice Semiconductor Inc.", "confidence": 0.99},
        "model": {"value": "GD5F1GQ5", "page": 1,
                  "quote": "GD5F1GQ5 NAND Flash", "confidence": 0.99},
        "device_type": {"value": "NAND Flash", "page": 1,
                        "quote": "NAND Flash", "confidence": 0.99},
    })


def _extraction_response() -> str:
    _, allowed = ai._single_pass_schema("NAND Flash")
    fields = []
    for key in allowed:
        found = key == "pe_cycles"
        fields.append({
            "field_key": key,
            "value": "100000" if found else None,
            "unit": "cycles" if found else None,
            "condition": None,
            "scope_type": "product_family",
            "scope_values": [],
            "evidence": ({"source_id": "", "page": 1, "section": "Endurance",
                          "quote": "Minimum 100,000 Program/Erase Cycles"} if found else None),
            "conflict_evidence": [],
            "confidence": 0.99 if found else 0.0,
            "status": "found" if found else "missing",
            "derived": False,
            "knowledge_type": "specification",
        })
    return json.dumps({"fields": fields})


def test_runtime_openai_mock_storage_pdf_to_reviewed_fact_golden_path(tmp_path, monkeypatch):
    project_root = Path(__file__).resolve().parent.parent
    runtime_root = project_root.parent.parent
    source_text = (
        "GigaDevice Semiconductor Inc.\nGD5F1GQ5 NAND Flash\n"
        "Endurance\nMinimum 100,000 Program/Erase Cycles\n"
    )
    pages = [(1, source_text, "markdown_text")]

    server = create_server("127.0.0.1", 0, state=_SequenceMockState([
        _identity_response(), _extraction_response(),
    ]))
    thread = threading.Thread(target=server.serve_forever,
                              kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        model_config = tmp_path / "model.yaml"
        model_config.write_text(f"""
active_model: qwen_prod
models:
  qwen_prod:
    provider: openai_compatible
    base_url: http://{host}:{port}/v1
    api_key_env: STORAGE_AGENT_API_KEY
    model: mock-gpt
    temperature: 0
    max_tokens: 8192
    metadata:
      capability_source: CONFIGURED
      capabilities:
        structured_output: unsupported
        token_limit_parameter: max_tokens
""".strip(), encoding="utf-8")
        monkeypatch.setenv("UNIFIED_AGENT_RUNTIME_ROOT", str(runtime_root))
        monkeypatch.setenv("STORAGE_LIFE_ALLOW_UNPINNED_RUNTIME", "1")
        monkeypatch.setenv("STORAGE_LIFE_EXECUTION_MODE", "runtime")
        monkeypatch.setenv("STORAGE_MODEL_CONFIG", str(model_config))
        monkeypatch.setenv("STORAGE_AGENT_API_KEY", "mock-secret")
        monkeypatch.setenv("STORAGE_LIFE_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
        monkeypatch.delenv("RUNTIME_PROVIDER_TRACE", raising=False)
        monkeypatch.delenv("RUNTIME_PROVIDER_DIAGNOSTICS", raising=False)
        monkeypatch.setattr(core, "DATA", tmp_path)
        monkeypatch.setattr(core, "DB", tmp_path / "storage.sqlite3")
        monkeypatch.setattr(core, "extract_pdf", lambda *_args, **_kwargs: pages)
        monkeypatch.setattr(core, "extract_pdf_pages", lambda *_args, **_kwargs: pages)
        monkeypatch.setattr(document_pipeline, "build_markdown", lambda *_args, **_kwargs: (
            "# Page 1\n" + source_text, pages,
            {"parser_version": "mock-golden-path", "markdown_sha256": "synthetic", "table_count": 0},
        ))
        # These ancillary calls do not define the path under test; identity recognition and
        # specification extraction below both use the actual Unified Runtime adapter.
        monkeypatch.setattr(ai, "identify_document_identity", lambda *_args, **_kwargs: {})
        monkeypatch.setattr(ai, "identify_models", lambda *_args, **_kwargs: {
            "models": [], "analyzed_pages": [1],
        })
        runtime_bridge._RUNTIME = None

        client = TestClient(app)
        pdf = ("gd5f1gq5.pdf", b"synthetic PDF for Runtime Mock test", "application/pdf")

        identified = client.post("/api/documents/identify", files={"file": pdf})
        assert identified.status_code == 200, identified.text
        identity = identified.json()
        assert identity["vendor"]["value"] == "GigaDevice"
        assert identity["model"]["value"] == "GD5F1GQ5"
        assert identity["device_type"]["value"] == "NAND Flash"

        created = client.post("/api/documents/jobs", files={
            "file": ("gd5f1gq5.pdf", b"synthetic PDF for Runtime Mock test", "application/pdf"),
        }, data={"vendor": "GigaDevice", "model": "GD5F1GQ5",
                 "device_type": "NAND Flash", "models_json": "[]"})
        assert created.status_code == 202, created.text
        job_id = created.json()["job_id"]
        job = None
        for _ in range(300):
            job = client.get(f"/api/documents/jobs/{job_id}").json()
            if job["status"] in {"completed", "failed"}:
                break
            time.sleep(0.01)
        assert job and job["status"] == "completed", job
        result = job["result"]
        assert result["candidate_count"] == 1
        assert result["coverage"]["states"]
        device_id = result["device_id"]

        workbench = client.get(f"/api/product/devices/{device_id}/review-workbench")
        assert workbench.status_code == 200
        row = next(row for row in workbench.json()["rows"]
                   if row["canonical_name"] == "pe_cycles")
        assert row["coverage_status"] == "FOUND"
        assert row["evidence"][0]["source_text"] == (
            "Endurance\nMinimum 100,000 Program/Erase Cycles"
        )

        confirmed = client.patch(f"/api/candidates/{row['candidate_id']}", json={
            "status": "confirmed", "value": "100000", "unit": "cycles",
            "verified_by": "runtime-mock-golden-path",
        })
        assert confirmed.status_code == 200, confirmed.text
        facts = client.get(f"/api/product/devices/{device_id}/facts")
        fact = next(item for item in facts.json()["facts"]
                    if item["canonical_name"] == "pe_cycles")
        assert fact["value"] == "100000"
        assert fact["evidence"][0]["source_text"] == (
            "Endurance\nMinimum 100,000 Program/Erase Cycles"
        )

        executions = runtime_bridge.last_executions()
        assert len([row for row in executions if row.get("status") == "COMPLETED"]) >= 2
    finally:
        runtime_bridge._RUNTIME = None
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
