from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.request import Request, urlopen

from fastapi.testclient import TestClient

from quality_knowledge.quality_scenario_v1_store import SQLiteQualityScenarioV1Repository
from quality_knowledge.scenario_assets import ScenarioAssets
from quality_knowledge.scenarios import ScenarioRepository
from quality_knowledge.web.app import create_app
from tools.build_quality_scenario_test_fixture import build_fixture
from tools.openai_mock.server import create_server
from tools.quality_scenario_functional_provider import FUNCTIONAL_RESPONSE


@contextmanager
def running_mock():
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


def configure_mock(host: str, port: int):
    raw = json.dumps(
        {"scenario_key": "default", "payload": FUNCTIONAL_RESPONSE, "behavior": {}},
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


def write_model_config(path: Path, host: str, port: int):
    path.write_text(
        f"""
active_model: qwen_prod
models:
  qwen_prod:
    provider: openai_compatible
    base_url: http://{host}:{port}/v1
    api_key_env: W4_FUNCTIONAL_MOCK_API_KEY
    model: mock-gpt
    temperature: 0
    max_tokens: 4096
""".strip(),
        encoding="utf-8",
    )


def wait_task(client: TestClient, task_id: str):
    last = None
    for _ in range(100):
        response = client.get(
            f"/api/v2/software-assessment/quality-scenario/generations/{task_id}"
        )
        assert response.status_code == 200, response.text
        last = response.json()
        states = {item.get("state") for item in last.get("items") or []}
        if not states.intersection({"ANALYZING"}):
            return last
        time.sleep(0.02)
    raise AssertionError(last)


def test_w4_controlled_provider_runs_g1_through_publish_and_portraits(tmp_path, monkeypatch):
    db = tmp_path / "w4-functional.db"
    manifest = build_fixture(db)
    g1 = next(item for item in manifest["cases"] if item["case_id"] == "G1_COMPLETE")

    with running_mock() as (host, port):
        configure_mock(host, port)
        model_config = tmp_path / "model.w4-functional.yaml"
        write_model_config(model_config, host, port)
        monkeypatch.setenv("W4_FUNCTIONAL_MOCK_API_KEY", "mock-secret")
        monkeypatch.setenv("REVERSE_QUALITY_MODEL_CONFIG", str(model_config))
        monkeypatch.setenv("QUALITY_SCENARIO_V1_DB_PATH", str(db))

        client = TestClient(create_app(db))
        payload = {
            "material_ids": [g1["software_assessment_material_id"]],
            "trigger_source": "HIGH_PERCEPTION",
            "trigger_reason": "W4_MAC_FUNCTIONAL_GOLDEN",
        }

        preview = client.post(
            "/api/v2/software-assessment/quality-scenario/preview",
            json=payload,
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["items"][0]["state"] == "READY"

        started = client.post(
            "/api/v2/software-assessment/quality-scenario/generations",
            json=payload,
        )
        assert started.status_code == 200, started.text
        task = wait_task(client, started.json()["task_id"])
        if task["items"][0]["state"] != "CANDIDATE_CREATED":
            rq_db = tmp_path / "reverse_quality_v01.db"
            diagnostic = []
            if rq_db.exists():
                with sqlite3.connect(rq_db) as connection:
                    connection.row_factory = sqlite3.Row
                    diagnostic = [
                        dict(row) for row in connection.execute(
                            "SELECT run_id,status,error,model FROM reverse_quality_run ORDER BY started_at"
                        ).fetchall()
                    ]
            raise AssertionError(
                {"task": task, "reverse_quality_runs": diagnostic}
            )
        scenario = task["items"][0]["scenario"]
        scenario_id = scenario["scenario_id"]
        assert scenario["blockers"] == []
        assert scenario["missing_information"] == []
        assert scenario["expected_result"]
        assert scenario["quality_concern_name"]

        reviewed = client.post(
            f"/api/v2/quality-scenario-workflow/v1/quality-scenarios/{scenario_id}/review",
            json={
                "expected_scenario_version": scenario["scenario_version"],
                "review_status": "CONFIRMED",
                "reviewer": "W4 Functional Reviewer",
                "comment": "受控功能验证：字段与来源证据可追溯",
            },
        )
        assert reviewed.status_code == 200, reviewed.text
        scenario = reviewed.json()["scenario"]
        assert scenario["review"]["review_status"] == "CONFIRMED"

        confirmed = client.post(
            f"/api/v2/quality-scenario-workflow/v1/quality-scenarios/{scenario_id}/confirm",
            json={
                "expected_scenario_version": scenario["scenario_version"],
                "quality_confirmed_by": "W4 Quality",
                "technical_confirmed_by": "W4 Technical",
                "confirmation_note": "Mac Functional Golden controlled-provider confirmation",
            },
        )
        assert confirmed.status_code == 200, confirmed.text
        scenario = confirmed.json()["scenario"]
        assert scenario["status"] == "CONFIRMED"

        published = client.post(
            f"/api/v2/quality-scenario-workflow/v1/quality-scenarios/{scenario_id}/publish",
            json={
                "expected_scenario_version": scenario["scenario_version"],
                "published_by": "W4 Functional Golden",
            },
        )
        assert published.status_code == 200, published.text
        scenario = published.json()["scenario"]
        assert scenario["status"] == "PUBLISHED"

        trace = client.get(
            f"/api/v2/quality-scenario-workflow/v1/quality-scenarios/{scenario_id}/traceability"
        )
        history = client.get(
            f"/api/v2/quality-scenario-workflow/v1/quality-scenarios/{scenario_id}/history"
        )
        assert trace.status_code == 200, trace.text
        assert history.status_code == 200, history.text

        projected = ScenarioAssets(
            ScenarioRepository(db),
            SQLiteQualityScenarioV1Repository(db),
        ).catalog()
        qsv1_asset = next(item for item in projected if item["scenario_id"] == scenario_id)
        assert qsv1_asset["source_of_truth"] == "QSV1"
        assert qsv1_asset["status"] == "PUBLISHED"

        for axis, marker in (
            ("product", "PLC-X200"),
            ("industry", "新能源"),
            ("customer", "客户A"),
        ):
            portrait = client.get(
                "/quality-scenario-assets/portrait",
                params={"portrait_axis": axis},
            )
            assert portrait.status_code == 200, portrait.text
            assert marker in portrait.text, (axis, marker)

        p04 = client.post(
            "/api/v2/quality-scenario-insights/v1/query",
            json={"view": "PRODUCT"},
        )
        assert p04.status_code == 200, p04.text
        p04_payload = p04.json()
        assert p04_payload["total"] == 1
        cards = {item["metric_key"]: item["value"] for item in p04_payload["stat_cards"]}
        assert cards["SOURCE_PROBLEM_COUNT"] == 1
        assert cards["CUSTOMER_COVERAGE_COUNT"] == 1
        assert cards["QUALITY_FOCUS_TYPE_COUNT"] == 1
        p04_item = p04_payload["scenario_list"][0]
        assert p04_item["source_problem_count"] == 1
        assert p04_item["product_context"] == "PLC-X200"
        assert p04_item["customer_context"] == "客户A"
        assert p04_item["industry_context"] == "新能源"
        assert p04_item["quality_focus"]

        for view in ("CUSTOMER", "INDUSTRY"):
            result = client.post(
                "/api/v2/quality-scenario-insights/v1/query",
                json={"view": view},
            )
            assert result.status_code == 200, result.text
            assert result.json()["total"] == 1

        portrait_api = client.post(
            "/api/v2/quality-scenario-insights/v1/customer-quality-portrait/v1/query",
            json={},
        )
        assert portrait_api.status_code == 200, portrait_api.text
        portrait_payload = portrait_api.json()
        assert portrait_payload["state"] == "NORMAL"
        assert portrait_payload["input_count"] == 1
        assert portrait_payload["result"]["quality_focus_distribution"]

        portrait_job = client.post(
            "/api/v2/quality-scenario-insights/v1/customer-quality-portrait-archive/v1/jobs",
            json={"filters": {"customer_ref": "客户A"}},
        )
        assert portrait_job.status_code == 201, portrait_job.text
        job_id = portrait_job.json()["job_id"]
        archived = client.post(
            f"/api/v2/quality-scenario-insights/v1/customer-quality-portrait-archive/v1/jobs/{job_id}/archive"
        )
        assert archived.status_code == 200, archived.text
        archive_id = archived.json()["archive_id"]
        archive_list = client.get(
            "/api/v2/quality-scenario-insights/v1/customer-quality-portrait-archive/v1"
        )
        assert archive_list.status_code == 200
        assert archive_list.json()["total"] == 1
        archive_detail = client.get(
            f"/api/v2/quality-scenario-insights/v1/customer-quality-portrait-archive/v1/{archive_id}"
        )
        assert archive_detail.status_code == 200, archive_detail.text
        assert archive_detail.json()["input_count"] == 1
