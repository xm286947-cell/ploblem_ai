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
from tools.build_quality_scenario_test_fixture import advance_g5, build_fixture
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


def _publish(client: TestClient, scenario: dict) -> dict:
    scenario_id = scenario["scenario_id"]
    reviewed = client.post(
        f"/api/v2/quality-scenario-workflow/v1/quality-scenarios/{scenario_id}/review",
        json={
            "expected_scenario_version": scenario["scenario_version"],
            "review_status": "CONFIRMED",
            "reviewer": "W4 Functional Reviewer",
            "comment": "W4 lineage validation",
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    scenario = reviewed.json()["scenario"]
    confirmed = client.post(
        f"/api/v2/quality-scenario-workflow/v1/quality-scenarios/{scenario_id}/confirm",
        json={
            "expected_scenario_version": scenario["scenario_version"],
            "quality_confirmed_by": "W4 Quality",
            "technical_confirmed_by": "W4 Technical",
            "confirmation_note": "W4 lineage validation",
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    scenario = confirmed.json()["scenario"]
    published = client.post(
        f"/api/v2/quality-scenario-workflow/v1/quality-scenarios/{scenario_id}/publish",
        json={
            "expected_scenario_version": scenario["scenario_version"],
            "published_by": "W4 Functional Golden",
        },
    )
    assert published.status_code == 200, published.text
    return published.json()["scenario"]


def test_w4_fixture_g4_idempotency_and_g5_source_revision_use_product_flow(tmp_path, monkeypatch):
    db = tmp_path / "w4-functional-lineage.db"
    manifest = build_fixture(db)
    by_case = {item["case_id"]: item for item in manifest["cases"]}

    with running_mock() as (host, port):
        configure_mock(host, port)
        model_config = tmp_path / "model.w4-functional.yaml"
        write_model_config(model_config, host, port)
        monkeypatch.setenv("W4_FUNCTIONAL_MOCK_API_KEY", "mock-secret")
        monkeypatch.setenv("REVERSE_QUALITY_MODEL_CONFIG", str(model_config))
        monkeypatch.setenv("QUALITY_SCENARIO_V1_DB_PATH", str(db))

        client = TestClient(create_app(db))

        g4_payload = {
            "material_ids": [by_case["G4_DUPLICATE_GENERATE"]["software_assessment_material_id"]],
            "trigger_source": "HIGH_PERCEPTION",
            "trigger_reason": "W4_MAC_FUNCTIONAL_GOLDEN",
        }
        g4_preview = client.post(
            "/api/v2/software-assessment/quality-scenario/preview", json=g4_payload
        )
        assert g4_preview.status_code == 200, g4_preview.text
        g4_item = g4_preview.json()["items"][0]
        assert g4_item["state"] == "READY"
        assert g4_item["bundle"]["source_status"]["ITR"] == "PRESENT"

        first = client.post(
            "/api/v2/software-assessment/quality-scenario/generations", json=g4_payload
        )
        assert first.status_code == 200, first.text
        first_task = wait_task(client, first.json()["task_id"])
        assert first_task["items"][0]["state"] == "CANDIDATE_CREATED", first_task
        first_id = first_task["items"][0]["scenario"]["scenario_id"]

        duplicate = client.post(
            "/api/v2/software-assessment/quality-scenario/generations", json=g4_payload
        )
        assert duplicate.status_code == 200, duplicate.text
        duplicate_item = duplicate.json()["items"][0]
        assert duplicate_item["state"] == "EXISTING_CANDIDATE", duplicate_item
        assert duplicate_item["scenario"]["scenario_id"] == first_id

        g5_payload = {
            "material_ids": [by_case["G5_SOURCE_REVISION"]["software_assessment_material_id"]],
            "trigger_source": "HIGH_PERCEPTION",
            "trigger_reason": "W4_MAC_FUNCTIONAL_GOLDEN",
        }
        before = client.post(
            "/api/v2/software-assessment/quality-scenario/preview", json=g5_payload
        )
        assert before.status_code == 200, before.text
        before_item = before.json()["items"][0]
        assert before_item["state"] == "READY"
        assert before_item["bundle"]["source_status"]["ITR"] == "PRESENT"
        before_revision = before_item["bundle"]["bundle_revision"]

        initial = client.post(
            "/api/v2/software-assessment/quality-scenario/generations", json=g5_payload
        )
        assert initial.status_code == 200, initial.text
        initial_task = wait_task(client, initial.json()["task_id"])
        assert initial_task["items"][0]["state"] == "CANDIDATE_CREATED", initial_task
        original = _publish(client, initial_task["items"][0]["scenario"])
        original_id = original["scenario_id"]
        assert original["status"] == "PUBLISHED"

        advance_g5(db)

        revised_preview = client.post(
            "/api/v2/software-assessment/quality-scenario/preview", json=g5_payload
        )
        assert revised_preview.status_code == 200, revised_preview.text
        revised_item = revised_preview.json()["items"][0]
        assert revised_item["state"] == "SOURCE_CHANGED_REANALYSIS_AVAILABLE", revised_item
        assert revised_item["bundle"]["source_status"]["ITR"] == "PRESENT"
        assert revised_item["bundle"]["bundle_revision"] != before_revision

        revised = client.post(
            "/api/v2/software-assessment/quality-scenario/generations", json=g5_payload
        )
        assert revised.status_code == 200, revised.text
        revised_task = wait_task(client, revised.json()["task_id"])
        assert revised_task["items"][0]["state"] == "CANDIDATE_CREATED", revised_task
        revised_id = revised_task["items"][0]["scenario"]["scenario_id"]
        assert revised_id != original_id

        old = client.get(
            f"/api/v2/quality-scenario-workflow/v1/quality-scenarios/{original_id}"
        )
        history = client.get(
            f"/api/v2/quality-scenario-workflow/v1/quality-scenarios/{original_id}/history"
        )
        assert old.status_code == 200
        assert old.json()["status"] == "PUBLISHED"
        assert history.status_code == 200
        assert len(history.json()["versions"]) >= 4
