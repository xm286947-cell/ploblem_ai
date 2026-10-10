"""Capture screenshots from the actual restored standalone FastAPI app in Chromium.

This uses a temporary empty isolated database, NEVER a customer's original DB.
It is a visual smoke test, not a customer-data or real-provider acceptance.
"""
from __future__ import annotations
import argparse
import json
import socket
import tempfile
import threading
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen
from urllib.parse import urljoin

import uvicorn
from playwright.sync_api import sync_playwright
from quality_knowledge.web.app import create_app

ROUTES = {
    "01-issue-workbench": ("/issues", "问题工作台"),
    "02-itr-original": ("/materials/itr", "ITR问题工作台"),
    "03-resolution-original": ("/materials/cs", "ITR彻底解决工作台"),
    "04-software-assessment-original": ("/materials/software-operations", "软件问题考核工作台"),
    "08-itr-compatibility": ("/itr/recovery-workbench", "ITR / 现场恢复"),
    "09-resolution-compatibility": ("/itr/resolution-workbench", "ITR 彻底解决工作台"),
    "10-software-assessment-compatibility": ("/software-assessment", "软件考核工作台"),
    "05-missed-test-analysis": ("/missed-test-analysis", "软件问题漏测分析"),
    "06-legacy-scenario-library": ("/quality-scenarios", "质量场景"),
    "07-legacy-scenario-assets": ("/quality-scenario-assets", "场景"),
    "08-original-itr": ("/materials/itr", "ITR问题工作台"),
    "09-original-resolution": ("/materials/cs", "ITR彻底解决工作台"),
    "10-original-software-assessment": ("/materials/software-operations", "软件问题考核工作台"),
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="restore-page-screenshots")
    args = parser.parse_args()
    destination = Path(args.output).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="legacy-restore-chrome-") as tmp:
        app = create_app(Path(tmp) / "isolated_chrome.db")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        address = f"http://127.0.0.1:{port}"
        for _ in range(90):
            try:
                with urlopen(address + "/issues", timeout=1) as res:
                    if res.status == 200:
                        break
            except (URLError, OSError):
                time.sleep(0.2)
        else:
            raise RuntimeError("RESTORED_APP_COULD_NOT_START")
        records = []
        try:
            with sync_playwright() as tool:
                browser = tool.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 960}, device_scale_factor=1)
                for name, (route, label) in ROUTES.items():
                    res = page.goto(address + route, wait_until="networkidle", timeout=30000)
                    if res is None or res.status != 200:
                        raise RuntimeError(f"BROWSER_ROUTE_ERROR {route} {res.status if res else 'none'}")
                    page.get_by_role("heading", name=label, exact=False).first.wait_for(timeout=5000)
                    page.evaluate("document.fonts.ready")
                    styles = page.locator("link[rel=stylesheet]")
                    assert styles.count() > 0, f"CSS_LINK_MISSING {route}"
                    for link in styles.all():
                        url = link.get_attribute("href")
                        if url and "app.css" in url:
                            resource = page.request.get(urljoin(address, url))
                            assert resource.status == 200, f"CSS_FETCH_FAILED {route} {resource.status}"
                    output = destination / (name + ".png")
                    page.screenshot(path=str(output), full_page=True, animations="disabled")
                    records.append({"path": route, "screenshot": output.name,
                                    "http_status": res.status, "heading": label})
                browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=8)
        (destination / "README.json").write_text(json.dumps({
            "test_kind": "REAL_CHROMIUM_VISUAL_SMOKE",
            "database": "ISOLATED_EMPTY_SQLITE_NOT_CUSTOMER_DATA",
            "provider": "NOT_USED",
            "release_gate": "NOT_CUSTOMER_ACCEPTANCE",
            "routes": records
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("CHROME_RESTORED_PAGES_CAPTURED=", len(records))


if __name__ == "__main__":
    main()
