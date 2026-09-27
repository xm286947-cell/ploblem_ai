from __future__ import annotations

import hashlib
import json
import socket
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from playwright.sync_api import sync_playwright

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.p04.fixtures import FixtureP04Provider
from quality_knowledge.web.p0_app import create_p0_app


ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _storage_stub() -> FastAPI:
    app = FastAPI()

    @app.get("/", response_class=HTMLResponse)
    def home():
        return '<a href="/p0/overall">Overall</a>'

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    return app


def _build_app(root: Path):
    db = root / "p0.db"
    P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    ).initialize(db)
    return create_p0_app(
        db,
        stage_runner=object(),
        p04_provider=FixtureP04Provider(),
        hardware_case_db_path=root / "hardware.sqlite3",
        hardware_tree_upload_dir=root / "tree_uploads",
        hardware_case_source_root=root / "sources",
        storage_app=_storage_stub(),
    )


def _wait_ready(base_url: str) -> None:
    import urllib.request

    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base_url + "/p0/quality-scenario-insights", timeout=1) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.1)
    raise RuntimeError("browser evidence server did not become ready")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="qs-p04-browser-") as temp:
        temp_root = Path(temp)
        port = _free_port()
        app = _build_app(temp_root)
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{port}"
        _wait_ready(base_url)

        evidence: dict[str, object] = {
            "navigations": [],
            "selectors_requests": [],
            "query_requests": [],
            "query_responses": [],
            "console_errors": [],
        }

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context()
                page = context.new_page()

                def on_navigation(frame):
                    if frame == page.main_frame:
                        evidence["navigations"].append(frame.url)

                def on_request(request):
                    if "/api/v2/quality-scenario-insights/v1/selectors" in request.url:
                        evidence["selectors_requests"].append(request.url)
                    if request.url.endswith("/api/v2/quality-scenario-insights/v1/query") and request.method == "POST":
                        try:
                            body = request.post_data_json
                        except Exception:
                            body = request.post_data
                        evidence["query_requests"].append(body)

                def on_response(response):
                    if response.url.endswith("/api/v2/quality-scenario-insights/v1/query"):
                        try:
                            body = response.json()
                        except Exception as exc:
                            body = {"response_error": str(exc)}
                        evidence["query_responses"].append(body)

                page.on("framenavigated", on_navigation)
                page.on("request", on_request)
                page.on("response", on_response)
                page.on("console", lambda msg: evidence["console_errors"].append(msg.text) if msg.type == "error" else None)

                page.goto(base_url + "/p0/quality-scenario-insights", wait_until="networkidle")

                script_src = page.locator('script[src*="p04_insights.js"]').get_attribute("src")
                assert script_src
                served = page.request.get(base_url + script_src.split("?", 1)[0])
                served_bytes = served.body()
                served_hash = hashlib.sha256(served_bytes).hexdigest()
                url_hash = script_src.split("?v=", 1)[1]
                local_hash = hashlib.sha256(
                    (ROOT / "quality_knowledge/web/static/p04_insights.js").read_bytes()
                ).hexdigest()
                evidence["script_src"] = script_src
                evidence["script_url_hash"] = url_hash
                evidence["script_served_hash"] = served_hash
                evidence["script_local_hash"] = local_hash

                page.locator('[data-view="INDUSTRY"]').click()
                page.wait_for_load_state("networkidle")
                page.wait_for_function(
                    "() => document.querySelector('[data-context]')?.textContent.startsWith('INDUSTRY')"
                )
                evidence["before_detail_active_view"] = page.locator(".p04-tabs button.active").get_attribute("data-view")
                evidence["before_detail_count"] = page.locator("[data-scenario-list] tbody tr").count()
                evidence["before_detail_session"] = json.loads(
                    page.evaluate("sessionStorage.getItem('p04-context')")
                )

                detail_link = page.locator('a[data-p03="QS-FIX-002"]').first
                detail_href = detail_link.get_attribute("href")
                evidence["detail_href"] = detail_href
                assert detail_href
                detail_query = parse_qs(urlparse(detail_href).query)
                detail_context = json.loads(detail_query["return_context"][0])
                evidence["detail_return_context"] = detail_context
                assert detail_context["contract"] == "p04-query-context/v1"
                assert detail_context["view"] == "INDUSTRY"

                detail_link.click()
                page.wait_for_load_state("networkidle")
                assert page.locator("[data-p03-detail]").get_attribute("data-scenario-id") == "QS-FIX-002"

                source_details = page.locator('#source-trace details', has_text="PROBLEM-003")
                assert source_details.count() == 1
                source_details.locator("summary").click()
                source_link = source_details.locator('a[href*="/sources/PROBLEM-003"]').first
                source_link.click()
                page.wait_for_load_state("networkidle")
                assert "PROBLEM-003" in page.text_content("body")
                page.go_back(wait_until="networkidle")
                assert page.locator("[data-p03-detail]").get_attribute("data-scenario-id") == "QS-FIX-002"

                return_href = page.locator("a.p0-back").get_attribute("href")
                evidence["return_href"] = return_href
                assert return_href and "p04_context=" in return_href

                # Keep only the return-leg traffic so the first overwrite point can
                # be located from selectors -> query -> rendered DOM.
                evidence["selectors_requests"] = []
                evidence["query_requests"] = []
                evidence["query_responses"] = []

                page.locator("a.p0-back").click()
                page.wait_for_load_state("networkidle")
                page.wait_for_function(
                    "() => document.querySelector('[data-status]')?.textContent === '已更新'"
                )

                evidence["after_return_url"] = page.url
                evidence["after_return_active_view"] = page.locator(".p04-tabs button.active").get_attribute("data-view")
                evidence["after_return_count"] = page.locator("[data-scenario-list] tbody tr").count()
                evidence["after_return_context_text"] = page.locator("[data-context]").text_content()
                evidence["after_return_session"] = json.loads(
                    page.evaluate("sessionStorage.getItem('p04-context')")
                )

                # Refresh safety: the one-shot p04_context must already be consumed,
                # and the latest session state must remain INDUSTRY after reload.
                page.reload(wait_until="networkidle")
                page.wait_for_function(
                    "() => document.querySelector('[data-status]')?.textContent === '已更新'"
                )
                evidence["after_refresh_active_view"] = page.locator(".p04-tabs button.active").get_attribute("data-view")
                evidence["after_refresh_count"] = page.locator("[data-scenario-list] tbody tr").count()

                browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=5)

        print("P04_BROWSER_RUNTIME_EVIDENCE=" + json.dumps(evidence, ensure_ascii=False, sort_keys=True))

        assert evidence["script_url_hash"] == evidence["script_served_hash"] == evidence["script_local_hash"]
        assert evidence["before_detail_active_view"] == "INDUSTRY"
        assert evidence["after_return_active_view"] == "INDUSTRY"
        assert evidence["after_return_session"]["view"] == "INDUSTRY"
        assert evidence["after_refresh_active_view"] == "INDUSTRY"
        assert evidence["query_requests"], evidence
        assert evidence["query_requests"][-1]["view"] == "INDUSTRY", evidence
        assert evidence["query_responses"], evidence
        assert evidence["query_responses"][-1]["view"] == "INDUSTRY", evidence


if __name__ == "__main__":
    main()
