"""Developer-only real-browser smoke for the data-isolated Major MVP host."""
from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


ROOT = Path(
    os.getenv("MAJOR_MVP_SMOKE_ROOT") or Path(__file__).resolve().parents[1]
).expanduser().resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn
from playwright.sync_api import sync_playwright

from quality_knowledge.p0.repository import P0Repository
from scripts.major_mvp_product_start import build_app


SMOKE_ISSUE_ID = "K-MVP-BROWSER-SMOKE"
SMOKE_ISSUE_TITLE = "Candidate-only synthetic issue for Repeat Risk browser binding"


def _seed_issue(app) -> None:
    repository = app.state.p0_repository
    if repository is None:
        raise RuntimeError("P0_REPOSITORY_NOT_READY")
    P0Repository(repository.db_path).save_issue(
        knowledge_id=SMOKE_ISSUE_ID,
        business_issue_id="ITR-MVP-BROWSER-SMOKE",
        raw_json={"问题编号": "ITR-MVP-BROWSER-SMOKE", "问题描述": SMOKE_ISSUE_TITLE},
        normalized_snapshot={
            "ISSUE_FACT": {
                "business_issue_id": "ITR-MVP-BROWSER-SMOKE",
                "title": SMOKE_ISSUE_TITLE,
                "description": SMOKE_ISSUE_TITLE,
                "product": "PLC",
                "version": "SYNTHETIC",
                "scene": "PRODUCT_CANDIDATE_SMOKE",
            }
        },
        mapping_config_id="MAP-PLC-V1",
        mapping_config_version=1,
        standard_catalog_version_id="SFC-PLC-V1",
        source_file_sha256="synthetic-candidate-smoke-only",
        sheet_name="synthetic-smoke",
        row_number=1,
    )


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_ready(url: str, server: uvicorn.Server) -> None:
    deadline = time.monotonic() + 30
    last_error: Exception | None = None
    while time.monotonic() < deadline and not server.should_exit:
        try:
            with urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, URLError) as error:
            last_error = error
            time.sleep(0.15)
    raise RuntimeError(f"SMOKE_SERVER_NOT_READY: {last_error}")


def run_smoke() -> list[str]:
    results: list[str] = []
    with tempfile.TemporaryDirectory(prefix="major-mvp-browser-smoke-") as temp:
        data_root = Path(temp) / "candidate-data"
        app = build_app(data_root)
        _seed_issue(app)
        port = _free_port()
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
        )
        thread = threading.Thread(target=server.run, name="major-mvp-smoke-server", daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{port}"
        try:
            _wait_ready(base_url + "/p0/major-production", server)
            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(headless=True)
                except Exception:
                    # Reuse an installed stable Chrome when Playwright's
                    # bundled Chromium has not been provisioned on this host.
                    browser = playwright.chromium.launch(channel="chrome", headless=True)
                page = browser.new_page()
                static_failures: list[str] = []
                page.on(
                    "response",
                    lambda response: static_failures.append(
                        f"{response.status}:{response.url}"
                    )
                    if "/p0/static/" in response.url and response.status >= 400
                    else None,
                )

                for route, marker in (
                    ("/p0/major-production", "data-major-production"),
                    ("/p0/cases", "重大问题案例库"),
                    ("/p0/issues", "问题工作台"),
                ):
                    response = page.goto(base_url + route, wait_until="domcontentloaded")
                    if response is None or response.status != 200:
                        raise RuntimeError(f"BROWSER_PAGE_UNREACHABLE={route}")
                    if marker not in page.content():
                        raise RuntimeError(f"BROWSER_PAGE_MARKER_MISSING={route}:{marker}")
                    results.append(f"PAGE_{route}=PASS")

                historical_detail = page.goto(
                    base_url + "/p0/cases/CANDIDATE-SYNTHETIC-CASE",
                    wait_until="domcontentloaded",
                )
                if (
                    historical_detail is None
                    or historical_detail.status != 200
                    or not page.locator("[data-case-detail]").count()
                ):
                    raise RuntimeError("HISTORICAL_CASE_DETAIL_UI_UNREACHABLE")
                results.append("HISTORICAL_CASE_DETAIL_UI=PASS")

                detail_response = page.goto(
                    base_url + f"/p0/issues/{SMOKE_ISSUE_ID}", wait_until="domcontentloaded"
                )
                if detail_response is None or detail_response.status != 200:
                    raise RuntimeError("REPEAT_RISK_DETAIL_PAGE_UNREACHABLE")
                page.locator("[data-repeat-risk]").wait_for(state="visible", timeout=5000)
                page.wait_for_function(
                    "() => document.querySelector('[data-title]')?.textContent === "
                    "'Candidate-only synthetic issue for Repeat Risk browser binding'",
                    timeout=10000,
                )
                repeat_state = page.request.get(
                    base_url + f"/api/v2/issues/{SMOKE_ISSUE_ID}/repeat-risk"
                )
                if repeat_state.status != 200:
                    raise RuntimeError(f"REPEAT_RISK_API_UNREACHABLE={repeat_state.status}")
                if not page.locator("#repeat-risk [data-repeat-query]").count():
                    raise RuntimeError("REPEAT_RISK_UI_BINDING_MISSING")
                results.append("REPEAT_RISK_UI_AND_API=PASS")

                template = page.request.get(
                    base_url + "/api/v2/major-production/excel/template"
                )
                if template.status != 200 or not template.body().startswith(b"PK"):
                    raise RuntimeError("MAJOR_EXCEL_TEMPLATE_API_UNREACHABLE")
                results.append("MAJOR_EXCEL_TEMPLATE_API=PASS")

                openapi = page.request.get(base_url + "/openapi.json")
                if openapi.status != 200:
                    raise RuntimeError("MAJOR_PRODUCTION_API_UNREACHABLE")
                openapi_paths = openapi.json().get("paths", {})
                if "/api/v2/major-production/cases/{case_id}" not in openapi_paths:
                    raise RuntimeError("MAJOR_PRODUCTION_API_ROUTE_MISSING")
                results.append("MAJOR_PRODUCTION_API=PASS")

                historical = page.request.get(base_url + "/api/v2/historical-cases")
                if historical.status != 200:
                    raise RuntimeError(f"HISTORICAL_CASE_API_UNREACHABLE={historical.status}")
                if historical.json().get("contract_version") != "historical-case/v1":
                    raise RuntimeError("HISTORICAL_CASE_CONTRACT_MISMATCH")
                results.append("HISTORICAL_CASE_UI_AND_API=PASS")

                browser.close()
                if static_failures:
                    raise RuntimeError("STATIC_ASSET_FAILURES=" + ";".join(static_failures))
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            if thread.is_alive():
                raise RuntimeError("SMOKE_SERVER_DID_NOT_STOP")
    return results


def main() -> int:
    try:
        for result in run_smoke():
            print(result)
    except Exception as error:
        print(f"DEVELOPER_BROWSER_SMOKE=FAIL\nBLOCKER={type(error).__name__}:{error}")
        return 1
    print("DATA=TEMPORARY_SYNTHETIC_ONLY")
    print("DEVELOPER_BROWSER_SMOKE=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
