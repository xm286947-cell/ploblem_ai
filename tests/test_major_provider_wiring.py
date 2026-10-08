"""Canonical Major provider composition and fail-closed wiring gate."""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import threading
from typing import Iterator
from urllib.request import Request, urlopen

from fastapi.testclient import TestClient

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.web.p0_app import create_p0_app
from tools.openai_mock.server import create_server


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tests/golden/hardware_case_scenarios/A9001-LDO 输出振荡.docx"
FORMAL_MOCK_FIXTURE_ID = "MAJOR_REPEAT_V11_F02"
FORMAL_MOCK_FIXTURE = {
    "ISSUE_FACT": "设备在特定负载切换条件下偶发母线过压并触发保护停机",
    "ROOT_CAUSE": "负载突变下回馈能量释放路径与控制参数组合导致母线电压瞬态上升",
    "ACTION": "优化制动/回馈策略及相关控制参数，并补充边界工况验证",
    "VERIFICATION": "重复边界负载切换后未再复现母线过压保护",
}
WIRING_TEST_SECRET = "MAJOR_WIRING_TEST_SECRET_MUST_NOT_PERSIST"


@contextmanager
def running_provider() -> Iterator[tuple[str, int]]:
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


def configure_provider(host: str, port: int, payload: list[dict]) -> None:
    raw = json.dumps(
        {"scenario_key": "default", "payload": payload},
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


def provider_counters(host: str, port: int) -> dict[str, int]:
    with urlopen(f"http://{host}:{port}/__mock__/counters", timeout=2) as response:
        return json.loads(response.read().decode())["data"]


def formal_mock_provider(_provider_input, pending_specs, _context):
    """Return fixture-backed provider responses without creating business state."""
    responses = []
    for spec in pending_specs:
        unit_id = str(spec["unit_id"])
        if unit_id not in FORMAL_MOCK_FIXTURE:
            raise ValueError(f"UNSUPPORTED_MAJOR_PROVIDER_UNIT:{unit_id}")
        responses.append({
            "object_id": spec["object_id"],
            "data": {"content": FORMAL_MOCK_FIXTURE[unit_id]},
        })
    return responses


def _initialize_p0(db_path: Path) -> None:
    P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    ).initialize(db_path)


def _intake(client: TestClient) -> dict:
    response = client.post(
        "/api/v2/major-production/sources",
        data={
            "title": "Canonical provider wiring",
            "group_code": "CANONICAL-GATE",
            "domain": "PLC",
            "standard_itr": "ITR-CANONICAL-F02-001",
        },
        files={
            "file": (
                SOURCE.name,
                SOURCE.read_bytes(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_core_provider_none_keeps_fail_closed_503(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("MAJOR_MODEL_CONFIG", raising=False)
    monkeypatch.delenv("DASHSCOPE_BASE_URL", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    p0_db = tmp_path / "fail-closed.sqlite3"
    _initialize_p0(p0_db)
    client = TestClient(create_p0_app(
        p0_db,
        project_root=ROOT,
        major_case_db_path=tmp_path / "major.sqlite3",
        major_attachment_root=tmp_path / "attachments",
        major_artifact_root=tmp_path / "artifacts",
    ))

    created = _intake(client)
    response = client.post(
        f"/api/v2/major-production/cases/{created['case']['case_id']}/analysis"
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "MAJOR_ANALYSIS_PROVIDER_NOT_CONFIGURED"
    assert client.get("/api/v2/historical-cases").json()["total"] == 0


def test_standard_composition_loads_configured_major_provider(tmp_path: Path) -> None:
    with running_provider() as (host, port):
        model_config = tmp_path / "model.local.yaml"
        model_config.write_text(
            f"""
active_model: qwen_prod
models:
  qwen_prod:
    provider: openai_compatible
    base_url: http://{host}:{port}/v1
    api_key: {WIRING_TEST_SECRET}
    model: qwen3.8-max
    temperature: 0
    max_tokens: 8192
""".strip(),
            encoding="utf-8",
        )
        p0_db = tmp_path / "configured.sqlite3"
        _initialize_p0(p0_db)
        app = create_p0_app(
            p0_db,
            project_root=ROOT,
            runtime_model_config=model_config,
            major_case_db_path=tmp_path / "major.sqlite3",
            major_attachment_root=tmp_path / "attachments",
            major_artifact_root=tmp_path / "artifacts",
        )
        assert app.state.major_provider_status == {
            "configured": True,
            "source": "UNIFIED_RUNTIME_AGENT_CONFIG",
        }
        client = TestClient(app)
        created = _intake(client)
        case_id = created["case"]["case_id"]
        event_id = created["event"]["event_id"]
        version_id = created["document"]["version_id"]
        fragments = app.state.major_case_repository.fragments(version_id)
        assert fragments
        fragment_id = str(fragments[0]["fragment_id"])
        payload = [
            {
                "object_id": f"{event_id}:{entry_type}",
                "content": FORMAL_MOCK_FIXTURE[entry_type],
                "fragment_ids": [fragment_id],
                "confidence": 0.9,
                "explanation": "The cited source fragment supports this candidate.",
                "mechanism": "",
            }
            for entry_type in FORMAL_MOCK_FIXTURE
        ]
        configure_provider(host, port, payload)

        response = client.post(
            f"/api/v2/major-production/cases/{case_id}/analysis"
        )
        assert response.status_code == 200, response.text
        assert len(response.json()["candidates"]) == 4
        assert provider_counters(host, port)["default"] == 1
        assert app.state.major_case_production_service.provider is not None

        runtime_db = tmp_path / "major.sqlite3.runtime.db"
        with sqlite3.connect(runtime_db) as connection:
            persisted = "\n".join(connection.iterdump())
        assert WIRING_TEST_SECRET not in persisted


def test_formal_provider_wiring_returns_four_pending_candidates(tmp_path: Path) -> None:
    p0_db = tmp_path / "provider.sqlite3"
    _initialize_p0(p0_db)
    client = TestClient(create_p0_app(
        p0_db,
        project_root=ROOT,
        major_case_db_path=tmp_path / "major.sqlite3",
        major_attachment_root=tmp_path / "attachments",
        major_artifact_root=tmp_path / "artifacts",
        major_provider=formal_mock_provider,
    ))

    assert client.get("/p0/major-production").status_code == 200
    assert client.get("/api/v2/historical-cases").json()["total"] == 0
    created = _intake(client)
    case_id = created["case"]["case_id"]
    event_id = created["event"]["event_id"]
    repository = client.app.state.major_case_repository
    assert repository.entries(case_id) == []

    pending_specs = [
        {"object_id": f"{event_id}:{unit_id}", "unit_id": unit_id}
        for unit_id in FORMAL_MOCK_FIXTURE
    ]
    provider_responses = formal_mock_provider({}, pending_specs, {})
    assert len(provider_responses) == 4
    assert [item["object_id"] for item in provider_responses] == [
        item["object_id"] for item in pending_specs
    ]
    assert [item["data"]["content"] for item in provider_responses] == list(
        FORMAL_MOCK_FIXTURE.values()
    )
    assert all(set(item) == {"object_id", "data"} for item in provider_responses)
    assert all(set(item["data"]) == {"content"} for item in provider_responses)
    assert repository.entries(case_id) == []

    analysis = client.post(f"/api/v2/major-production/cases/{case_id}/analysis")
    assert analysis.status_code == 200, analysis.text
    candidates = analysis.json()["candidates"]
    assert len(candidates) == 4
    assert {item["entry_type"] for item in candidates} == set(FORMAL_MOCK_FIXTURE)
    assert {item["entry_type"]: item["content"] for item in candidates} == FORMAL_MOCK_FIXTURE
    assert all(item["status"] == "PENDING" and item["origin"] == "AI" for item in candidates)
    assert all(item["assertion_kind"] == "AI_INFERENCE" for item in candidates)
    assert client.get("/api/v2/historical-cases").json()["total"] == 0
    assert client.post(f"/api/v2/major-production/events/{event_id}/publish").status_code == 409
    assert FORMAL_MOCK_FIXTURE_ID == "MAJOR_REPEAT_V11_F02"
