from __future__ import annotations

import json
import threading
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi.testclient import TestClient

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.web.p0_app import create_p0_app
from tools.openai_mock.server import create_server


ROOT = Path(__file__).resolve().parents[1]
ROLE = {"X-Hardware-Case-Role": "MAINTAINER"}


def _synthetic_docx(path: Path) -> None:
    document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Symptom: Synthetic power rail drops during surge.</w:t></w:r></w:p>
    <w:p><w:r><w:t>Root cause: Synthetic protection margin is insufficient.</w:t></w:r></w:p>
    <w:p><w:r><w:t>Action: Increase synthetic protection margin.</w:t></w:r></w:p>
    <w:sectPr/>
  </w:body>
</w:document>"""
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document_xml)


def test_runtime_openai_mock_word_upload_creates_evidenced_hardware_candidate(tmp_path, monkeypatch):
    mock_payload = {
        "title": "Synthetic surge protection case",
        "product_context": {"product_line": "Synthetic controller"},
        "facts": {
            "symptom": {"value": "Synthetic power rail drops during surge.", "evidence_block_ids": ["B0001"]},
            "root_cause": {"value": "Synthetic protection margin is insufficient.", "evidence_block_ids": ["B0002"]},
            "actions": {"value": "Increase synthetic protection margin.", "evidence_block_ids": ["B0003"]},
        },
        "circuit_feature_links": [],
        "material_links": [],
    }
    server = create_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever,
                              kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        configure_body = json.dumps({
            "scenario_key": "default", "payload": mock_payload, "behavior": {},
        }, ensure_ascii=False).encode("utf-8")
        from urllib.request import Request, urlopen
        request = Request(f"http://{host}:{port}/__mock__/scenario", data=configure_body,
                          method="POST", headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=2) as response:
            assert response.status == 200

        model_config = tmp_path / "model.local.yaml"
        model_config.write_text(f"""
active_model: hardware_case_test
models:
  hardware_case_test:
    provider: openai_compatible
    base_url: http://{host}:{port}/v1
    api_key_env: HARDWARE_CASE_MOCK_KEY
    model: qwen-hardware-case-test
    temperature: 0
    max_tokens: 8192
""".strip(), encoding="utf-8")
        monkeypatch.setenv("HARDWARE_CASE_MODEL_CONFIG", str(model_config))
        monkeypatch.setenv("HARDWARE_CASE_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
        monkeypatch.setenv("HARDWARE_CASE_MOCK_KEY", "synthetic-mock-secret")

        p0_db = tmp_path / "p0.sqlite3"
        P0Initializer(
            manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
            plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
        ).initialize(p0_db)
        app = create_p0_app(
            p0_db,
            stage_runner=object(),
            hardware_case_db_path=tmp_path / "hardware.sqlite3",
            hardware_case_source_root=tmp_path / "sources",
        )
        client = TestClient(app)

        word = tmp_path / "A91234-synthetic surge case.docx"
        _synthetic_docx(word)
        uploaded = client.post(
            "/api/v2/hardware-cases/intakes",
            files={"file": (word.name, word.read_bytes(),
                             "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            headers=ROLE,
        )
        assert uploaded.status_code == 201, uploaded.text
        intake_id = uploaded.json()["intake_id"]
        processed = client.post(f"/api/v2/hardware-cases/intakes/{intake_id}/process", headers=ROLE)
        assert processed.status_code == 200, processed.text
        candidate = processed.json()
        assert candidate["status"] == "CANDIDATE_READY", candidate
        assert candidate["candidate"]["case_id"] == "A91234"
        assert candidate["candidate"]["facts"] == {
            "symptom": "Synthetic power rail drops during surge.",
            "root_cause": "Synthetic protection margin is insufficient.",
            "actions": "Increase synthetic protection margin.",
        }
        evidence = candidate["candidate"]["evidence"]
        assert {item["locator"]["block_id"] for item in evidence} == {"B0001", "B0002", "B0003"}
        case = client.get("/api/v2/hardware-cases/A91234", headers=ROLE)
        assert case.status_code == 200
        assert case.json()["processing_status"] != "PUBLISHED"
        source_view = client.get("/api/v2/hardware-cases/A91234/evidence", headers=ROLE)
        assert source_view.status_code == 200
        first = source_view.json()["evidence"][0]
        preview = client.get(
            f"/api/v2/hardware-cases/A91234/evidence/{first['evidence_id']}/source-preview",
            headers=ROLE,
        )
        assert preview.status_code == 200
        assert preview.json()["preview_status"] == "AVAILABLE"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
