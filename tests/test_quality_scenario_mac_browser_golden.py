from __future__ import annotations

import json
import socket
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen

import uvicorn

from quality_knowledge.web.app import create_app
from tools.build_quality_scenario_test_fixture import advance_g5, build_fixture
from tools.openai_mock.server import create_server
from tools.quality_scenario_functional_provider import FUNCTIONAL_RESPONSE, configure


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_http(url: str, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            with urlopen(url, timeout=0.5) as response:
                if response.status == 200:
                    return
        except Exception as error:  # pragma: no cover - diagnostic path
            last = error
            time.sleep(0.1)
    raise AssertionError(f"SERVER_NOT_READY:{url}:{last}")


@contextmanager
def _running_mock(port: int):
    server = create_server("127.0.0.1", port)
    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.01},
        daemon=True,
    )
    thread.start()
    try:
        configure("127.0.0.1", port)
        yield
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@contextmanager
def _running_app(app, port: int):
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            access_log=False,
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    _wait_http(f"http://127.0.0.1:{port}/issues")
    try:
        yield
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def _write_model_config(path: Path, port: int) -> None:
    path.write_text(
        f"""
active_model: qwen_prod
models:
  qwen_prod:
    provider: openai_compatible
    base_url: http://127.0.0.1:{port}/v1
    api_key_env: W4_FUNCTIONAL_MOCK_API_KEY
    model: mock-gpt
    temperature: 0
    max_tokens: 4096
""".strip(),
        encoding="utf-8",
    )


def _case(manifest: dict, case_id: str) -> dict:
    return next(item for item in manifest["cases"] if item["case_id"] == case_id)


def _prepare_case(page, base: str, material_id: str):
    page.goto(base + "/software-assessment", wait_until="networkidle")
    box = page.locator(f'[data-assessment-select][value="{material_id}"]')
    box.check()
    page.locator("[data-trigger-source]").select_option("HIGH_PERCEPTION")
    page.locator("[data-trigger-reason]").fill("MAC_BROWSER_GOLDEN")
    page.locator("[data-preview]").click()


def _scenario_id_from_actions(page) -> str:
    href = page.locator(
        '.sa-qsv1-actions a[href^="/quality-scenarios/workbench?scenario_id="]'
    ).first.get_attribute("href")
    assert href
    values = parse_qs(urlparse(href).query)
    return values["scenario_id"][0]


def _generate_candidate(page, base: str, material_id: str, expected_preview: str = "READY") -> str:
    _prepare_case(page, base, material_id)
    page.locator(f'[data-state="{expected_preview}"]').wait_for(timeout=15000)
    page.locator("[data-generate]").click()
    page.locator('[data-state="CANDIDATE_CREATED"]').wait_for(timeout=30000)
    return _scenario_id_from_actions(page)


def _publish_from_workbench(page, base: str, scenario_id: str) -> None:
    page.goto(
        base + "/quality-scenarios/workbench?scenario_id=" + scenario_id,
        wait_until="networkidle",
    )
    page.locator("[data-scenario-id]").wait_for(timeout=15000)
    assert page.locator("[data-scenario-id]").inner_text() == scenario_id
    page.locator("[data-quality-actor]").fill("Mac Golden Quality")
    page.locator("[data-technical-actor]").fill("Mac Golden Technical")
    page.locator("[data-confirmation-note]").fill("macOS development-in-test browser golden")
    page.locator('[data-action="confirm"]').click()
    page.locator("[data-scenario-status]").filter(has_text="Confirmed").wait_for(timeout=15000)
    page.locator('[data-action="publish"]').click()
    page.locator("[data-scenario-status]").filter(has_text="Published").wait_for(timeout=15000)


def test_mac_browser_g1_g5_from_mature_software_assessment(tmp_path, monkeypatch):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:  # pragma: no cover
        raise AssertionError("PLAYWRIGHT_REQUIRED_FOR_MAC_BROWSER_GOLDEN") from error

    source_db = tmp_path / "source_fixture.db"
    qsv1_db = tmp_path / "qsv1_result.db"
    manifest = build_fixture(source_db)

    mock_port = _free_port()
    app_port = _free_port()
    model_config = tmp_path / "model.mac-browser.yaml"
    _write_model_config(model_config, mock_port)

    monkeypatch.setenv("W4_FUNCTIONAL_MOCK_API_KEY", "test-only")
    monkeypatch.setenv("REVERSE_QUALITY_MODEL_CONFIG", str(model_config))
    monkeypatch.setenv("QUALITY_SCENARIO_V1_DB_PATH", str(qsv1_db))

    with _running_mock(mock_port):
        app = create_app(source_db)
        with _running_app(app, app_port):
            base = f"http://127.0.0.1:{app_port}"
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page()

                # G1: complete source -> Candidate -> Review/Confirm -> Publish -> Library/Detail/Portrait.
                g1 = _case(manifest, "G1_COMPLETE")
                g1_id = _generate_candidate(
                    page, base, g1["software_assessment_material_id"]
                )
                _publish_from_workbench(page, base, g1_id)

                page.goto(base + "/quality-scenarios/library", wait_until="networkidle")
                page.locator(f'[data-detail="{g1_id}"]').wait_for(timeout=15000)
                page.locator(f'[data-detail="{g1_id}"]').click()
                page.locator("[data-status]").filter(has_text="PUBLISHED").wait_for(timeout=15000)
                page.goto(
                    base + "/quality-scenario-assets/portrait?portrait_axis=product",
                    wait_until="networkidle",
                )
                assert "PLC-X200" in page.content()

                # G2: missing missed-test remains explicitly MISSING; no fake source is invented.
                g2 = _case(manifest, "G2_NO_MISSED_TEST")
                _prepare_case(page, base, g2["software_assessment_material_id"])
                page.locator('[data-state="READY"]').wait_for(timeout=15000)
                assert "漏测分析：MISSING" in page.locator("[data-flow-results]").inner_text()
                page.locator("[data-generate]").click()
                page.locator('[data-state="CANDIDATE_CREATED"]').wait_for(timeout=30000)
                g2_id = _scenario_id_from_actions(page)
                page.goto(
                    base + "/quality-scenarios/library/" + g2_id,
                    wait_until="networkidle",
                )
                page.locator("[data-detail]").wait_for(timeout=15000)
                detail_text = page.locator("[data-detail]").inner_text()
                assert "ESCAPE_ANALYSIS" not in detail_text

                # G3: conflicting Resolution is blocked in the mature UI and Generate stays disabled.
                g3 = _case(manifest, "G3_CONFLICT")
                _prepare_case(page, base, g3["software_assessment_material_id"])
                page.locator('[data-state="INFORMATION_REQUIRED"]').wait_for(timeout=15000)
                assert page.locator("[data-generate]").is_disabled()
                assert "彻底解决单：CONFLICT" in page.locator("[data-flow-results]").inner_text()

                # G4: second generation of the same frozen source reuses the existing Candidate.
                g4 = _case(manifest, "G4_DUPLICATE_GENERATE")
                g4_id = _generate_candidate(
                    page, base, g4["software_assessment_material_id"]
                )
                _prepare_case(page, base, g4["software_assessment_material_id"])
                page.locator('[data-state="EXISTING_CANDIDATE"]').wait_for(timeout=15000)
                assert _scenario_id_from_actions(page) == g4_id
                page.locator("[data-generate]").click()
                page.locator('[data-state="EXISTING_CANDIDATE"]').wait_for(timeout=15000)
                assert _scenario_id_from_actions(page) == g4_id

                # G5: source revision creates a new lineage while preserving the old published object/history.
                g5 = _case(manifest, "G5_SOURCE_REVISION")
                old_g5_id = _generate_candidate(
                    page, base, g5["software_assessment_material_id"]
                )
                _publish_from_workbench(page, base, old_g5_id)
                advance_g5(source_db)

                _prepare_case(page, base, g5["software_assessment_material_id"])
                page.locator(
                    '[data-state="SOURCE_CHANGED_REANALYSIS_AVAILABLE"]'
                ).wait_for(timeout=15000)
                page.locator("[data-generate]").click()
                page.locator('[data-state="CANDIDATE_CREATED"]').wait_for(timeout=30000)
                new_g5_id = _scenario_id_from_actions(page)
                assert new_g5_id != old_g5_id

                page.goto(
                    base + "/quality-scenarios/library/" + old_g5_id,
                    wait_until="networkidle",
                )
                page.locator("[data-status]").filter(has_text="PUBLISHED").wait_for(timeout=15000)
                history = page.locator("[data-history-count]").inner_text()
                assert "个版本" in history

                browser.close()
