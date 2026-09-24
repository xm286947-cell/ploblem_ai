from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("runtime")

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.reverse_quality_runtime import ReverseQualityRuntimeExecutor
from quality_knowledge.web.app import create_app
from quality_knowledge.web.p0_app import create_p0_app


ROOT = Path(__file__).resolve().parents[1]
GOLDEN_PATH = ROOT / "tests/golden/quality_scenario_rc1_golden_v01.json"
AGENT_CONFIG = ROOT / "config/runtime/agents/reverse_quality.single_issue.analyze.yaml"
SECRET = "QS_RC1_GOLDEN_RUNTIME_SECRET"


def _golden() -> dict:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


class _ProviderState:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls = 0
        self.requests: list[dict] = []


@contextmanager
def _running_provider(payload: dict):
    state = _ProviderState(payload)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            state.calls += 1
            state.requests.append(
                {
                    "method": "POST",
                    "path": self.path,
                    "model": body.get("model"),
                    "authorization": self.headers.get("Authorization"),
                }
            )
            envelope = {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(state.payload, ensure_ascii=False)
                        },
                        "finish_reason": "stop",
                    }
                ]
            }
            raw = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _model_config(tmp_path: Path, base_url: str) -> Path:
    path = tmp_path / "model.local.yaml"
    path.write_text(
        f"""
models:
  qwen_prod:
    provider: openai_compatible
    base_url: {base_url}
    api_key: {SECRET}
    model: mock-gpt
    temperature: 0
    max_tokens: 8192
""".strip(),
        encoding="utf-8",
    )
    return path


def _executor(tmp_path: Path, base_url: str) -> ReverseQualityRuntimeExecutor:
    return ReverseQualityRuntimeExecutor(
        ROOT,
        tmp_path / "runtime.db",
        model_config_path=_model_config(tmp_path, base_url),
        agent_config_path=AGENT_CONFIG,
        environ={},
    )


def _runtime_bytes(tmp_path: Path) -> bytes:
    return b"".join(
        path.read_bytes()
        for path in sorted(tmp_path.glob("runtime.db*"))
        if path.is_file()
    )


def _initializer() -> P0Initializer:
    return P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    )


def test_rc1_golden_path_problem_to_runtime_reverse_candidate_publish_library_detail_evidence(tmp_path):
    golden = _golden()
    assert golden["classification"] == "SANITIZED_SYNTHETIC"
    assert golden["contains_internal_real_data"] is False

    reverse_db_path = tmp_path / "reverse_quality_source.db"
    p0_db_path = tmp_path / "quality_scenario_rc1.db"

    with _running_provider(golden["runtime_response"]) as (server, provider):
        host, port = server.server_address

        legacy_app = create_app(reverse_db_path)
        reverse_service = legacy_app.state.reverse_quality_service
        reverse_service.ai_client = None
        reverse_service._runtime_executor = _executor(
            tmp_path,
            f"http://{host}:{port}/v1",
        )

        material_repo = legacy_app.state.material_repository
        problem = golden["problem"]
        material_id, _ = material_repo.add_material(
            material_repo.group("ITR-CS"),
            problem["business_key"],
            problem["raw"],
            problem["source_file"],
            problem["sheet_name"],
            problem["row_number"],
        )

        reverse = reverse_service.analyse(material_id, golden["business_type"])
        assert provider.calls == 1
        assert provider.requests[0]["path"] == "/v1/chat/completions"
        assert provider.requests[0]["authorization"] == f"Bearer {SECRET}"
        assert reverse["result_version"] == "reverse-quality-v0.1"
        assert reverse["result"]["identity"]["canonical_itr"]
        assert reverse["result"]["fields"]["lifecycle_stage"]["value"] == "运行执行"
        assert reverse["result"]["fields"]["business_activity_scene"]["value"] == "掉电数据保持与上电恢复"
        assert reverse["missing_information"] == []

        taxonomy = legacy_app.state.scenario_repository.taxonomy_active(
            golden["business_type"]
        )
        assert taxonomy is not None

        # The Reverse Quality source domain and the V1 product domain keep their
        # independent stores. Handoff happens through ReverseQualityResult V0.1,
        # not through shared database tables.
        _initializer().initialize(p0_db_path)
        client = TestClient(create_p0_app(p0_db_path, stage_runner=None))
        created = client.post(
            "/api/v2/quality-scenarios/candidates/from-reverse",
            json={
                "reverse_quality_result": reverse["result"],
                "taxonomy": taxonomy,
                "trigger_source": golden["trigger_source"],
                "trigger_reason": golden["trigger_reason"],
                "created_by": "QS_RC1_GOLDEN",
            },
        )
        assert created.status_code == 200, created.text
        candidate = created.json()["scenario"]
        assert candidate["status"] == "CANDIDATE"
        assert candidate["trigger_source"] == "HIGH_PERCEPTION"
        assert candidate["trigger_reason"] == golden["trigger_reason"]
        assert candidate["blockers"] == []
        assert candidate["source_problem_refs"]
        assert candidate["evidence_refs"]

        human = golden["human_review"]
        reviewed = client.post(
            f"/api/v2/quality-scenarios/{candidate['scenario_id']}/review",
            json={
                "expected_scenario_version": candidate["scenario_version"],
                "patch": {},
                "review_status": "CONFIRMED",
                "reviewer": human["quality_reviewer"],
                "comment": "RC1 Golden Evidence核对完成",
            },
        )
        assert reviewed.status_code == 200, reviewed.text
        reviewed_scenario = reviewed.json()["scenario"]

        confirmed = client.post(
            f"/api/v2/quality-scenarios/{candidate['scenario_id']}/confirm",
            json={
                "expected_scenario_version": reviewed_scenario["scenario_version"],
                "quality_confirmed_by": human["quality_reviewer"],
                "technical_confirmed_by": human["technical_reviewer"],
                "confirmation_note": human["confirmation_note"],
            },
        )
        assert confirmed.status_code == 200, confirmed.text
        confirmed_scenario = confirmed.json()["scenario"]
        assert confirmed_scenario["status"] == "CONFIRMED"

        published = client.post(
            f"/api/v2/quality-scenarios/{candidate['scenario_id']}/publish",
            json={
                "expected_scenario_version": confirmed_scenario["scenario_version"],
                "published_by": human["quality_reviewer"],
            },
        )
        assert published.status_code == 200, published.text
        published_scenario = published.json()["scenario"]
        assert published_scenario["status"] == "PUBLISHED"
        assert published_scenario["version"]["published_at"]

        repeated = client.post(
            f"/api/v2/quality-scenarios/{candidate['scenario_id']}/publish",
            json={
                "expected_scenario_version": confirmed_scenario["scenario_version"],
                "published_by": human["quality_reviewer"],
            },
        )
        assert repeated.status_code == 200
        assert repeated.json()["changed"] is False
        assert repeated.json()["scenario"]["scenario_version"] == published_scenario["scenario_version"]

        assert provider.calls == 1

        library = client.get(
            "/api/v2/quality-scenarios",
            params={
                "status": "PUBLISHED",
                "product_code": golden["business_type"],
                "lifecycle_stage_code": "RUNTIME_EXECUTION",
                "business_activity_code": "POWER_LOSS_RETENTION_RECOVERY",
                "q": "数据完整性",
            },
        )
        assert library.status_code == 200, library.text
        ids = [item["scenario_id"] for item in library.json()["items"]]
        assert published_scenario["scenario_id"] in ids

        detail = client.get(
            f"/api/v2/quality-scenarios/{published_scenario['scenario_id']}"
        )
        assert detail.status_code == 200
        assert detail.json()["status"] == "PUBLISHED"
        assert detail.json()["confirmation"]["quality_confirmed_by"] == human["quality_reviewer"]
        assert detail.json()["confirmation"]["technical_confirmed_by"] == human["technical_reviewer"]

        history = client.get(
            f"/api/v2/quality-scenarios/{published_scenario['scenario_id']}/history"
        )
        assert history.status_code == 200, history.text
        assert history.json()["versions"]
        assert history.json()["reviews"]

        trace = client.get(
            f"/api/v2/quality-scenarios/{published_scenario['scenario_id']}/traceability"
        )
        assert trace.status_code == 200, trace.text
        trace_body = trace.json()
        assert trace_body["integrity"]["status"] == "PASS"
        assert trace_body["integrity"]["issues"] == []
        assert trace_body["sources"]
        assert trace_body["evidence"]
        assert all(item["source_resolved"] for item in trace_body["evidence"])
        assert all(item["supports_valid"] for item in trace_body["evidence"])

        source_ref = published_scenario["source_problem_refs"][0]["source_ref"]
        reverse_lookup = client.get(
            "/api/v2/quality-scenario-sources/scenarios",
            params={"source_ref": source_ref},
        )
        assert reverse_lookup.status_code == 200
        linked_ids = [item["scenario_id"] for item in reverse_lookup.json()["items"]]
        assert published_scenario["scenario_id"] in linked_ids

        p01 = client.get("/p0/quality-scenarios/workbench")
        p02 = client.get("/p0/quality-scenarios")
        p03 = client.get(f"/p0/quality-scenarios/{published_scenario['scenario_id']}")
        assert p01.status_code == 200 and "场景工作台" in p01.text
        assert p02.status_code == 200 and "质量场景库" in p02.text
        assert 'value="PUBLISHED" selected' in p02.text
        assert p03.status_code == 200 and "场景详情" in p03.text
        assert "Evidence完整性" in p03.text

        assert SECRET.encode("utf-8") not in _runtime_bytes(tmp_path)


def test_rc1_golden_dataset_contains_no_internal_real_data_marker():
    golden = _golden()
    assert golden["contains_internal_real_data"] is False
    raw = GOLDEN_PATH.read_text(encoding="utf-8")
    assert "SANITIZED_SYNTHETIC" in raw
    assert "internal_real_data" in raw
