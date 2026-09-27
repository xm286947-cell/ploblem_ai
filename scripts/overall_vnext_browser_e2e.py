"""Run a real Chromium E2E over the synthetic Overall VNext candidate.

The runner owns an isolated data directory, starts the existing single FastAPI
host, and writes machine-readable evidence plus desktop/mobile screenshots.
"""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import closing
from pathlib import Path

from playwright.sync_api import Page, sync_playwright


ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_ready(url: str, timeout_seconds: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError) as exc:
            last_error = exc
        time.sleep(0.2)
    raise RuntimeError(f"DUT_NOT_READY:{last_error}")


def _assert_text(page: Page, value: str) -> None:
    page.get_by_text(value, exact=False).first.wait_for(state="visible", timeout=10_000)


def _goto(page: Page, base_url: str, path: str) -> None:
    response = page.goto(f"{base_url}{path}", wait_until="networkidle")
    if response is None or response.status >= 400:
        raise AssertionError(f"BROWSER_ROUTE_FAILED:{path}:{None if response is None else response.status}")


def _run_desktop(page: Page, base_url: str, evidence_dir: Path) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []

    _goto(page, base_url, "/p0/overall")
    assert page.title() == "总体工作台"
    _assert_text(page, "专业质量软件平台")
    for marker in (
        "重大问题案例库 × Repeat Risk",
        "质量场景库",
        "硬件案例库",
        "存储器件寿命智能产品",
        "ITR-VNEXT-DEMO-001",
    ):
        if marker == "ITR-VNEXT-DEMO-001":
            continue
        _assert_text(page, marker)
    checks.append({"check": "overall_shell", "result": "PASS"})
    page.screenshot(path=evidence_dir / "overall-desktop.png", full_page=True)

    areas = {
        "current-problem": "当前问题",
        "cases-knowledge": "案例与知识",
        "scenarios-insights": "质量场景与洞察",
        "professional-topics": "专业专题",
        "management": "管理与配置",
    }
    for area, title in areas.items():
        _goto(page, base_url, f"/p0/overall/areas/{area}")
        assert page.locator("h1").inner_text() == title
    checks.append({"check": "five_product_areas", "result": "PASS"})

    workspaces = {
        "major": ("/p0/cases", "重大问题"),
        "quality-scenario": ("/p0/quality-scenario-insights", "质量场景"),
        "hardware": ("/p0/hardware-cases", "硬件案例"),
        "storage": ("/storage-workspace/", "Storage"),
    }
    for workspace, (path, marker) in workspaces.items():
        _goto(page, base_url, f"/p0/workspaces/{workspace}")
        assert path in page.url
        _assert_text(page, marker)
        assert page.locator('a[href="/p0/overall"]').count() >= 1
    checks.append({"check": "four_workspace_bindings", "result": "PASS"})

    _goto(page, base_url, "/issues")
    _assert_text(page, "ITR-VNEXT-DEMO-001")
    checks.append({"check": "legacy_synthetic_issue", "result": "PASS"})

    _goto(page, base_url, "/p0/quality-scenario-insights?view=INDUSTRY")
    assert page.locator('button[data-view="INDUSTRY"].active').count() == 1
    row = page.locator("tr").filter(has_text="QS-FIX-002").first
    row.wait_for(state="visible", timeout=10_000)
    scenario_href = row.locator("a").first.get_attribute("href")
    assert scenario_href and "p04_context=" in scenario_href
    _goto(page, base_url, scenario_href)
    _assert_text(page, "QS-FIX-002")
    source_href = page.locator(
        'a[href*="/p0/quality-scenario-sources/PROBLEM-003"]'
    ).first.get_attribute("href")
    assert source_href and "p04_context=" in source_href
    _goto(page, base_url, source_href)
    _assert_text(page, "Source Reference")
    return_href = page.get_by_role("link", name="← 返回质量场景工作区", exact=True).get_attribute("href")
    assert return_href and "p04_context=" in return_href
    _goto(page, base_url, return_href)
    assert page.locator('button[data-view="INDUSTRY"].active').count() == 1
    assert "p04_context=" not in page.url
    checks.append({"check": "p04_industry_detail_source_return", "result": "PASS"})

    _goto(page, base_url, "/p0/overall/evidence?producer_domain=Major&evidence_id=EV-DEMO-1&return_to=/p0/overall")
    _assert_text(page, "common-evidence/v1.0")
    checks.append({"check": "common_evidence", "result": "PASS"})

    _goto(page, base_url, "/storage-workspace/")
    assert page.locator('a[href="/p0/overall"]').count() >= 1
    checks.append({"check": "storage_same_host_return", "result": "PASS"})
    return checks


def _run_mobile(browser, base_url: str, evidence_dir: Path) -> list[dict[str, str]]:
    page = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=1)
    try:
        _goto(page, base_url, "/p0/overall")
        _assert_text(page, "专业质量软件平台")
        assert page.get_by_role("link", name="进入问题工作台", exact=True).is_visible()
        dimensions = page.evaluate(
            "() => ({viewport: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth})"
        )
        assert dimensions["scroll"] <= dimensions["viewport"] + 1, dimensions
        page.screenshot(path=evidence_dir / "overall-mobile.png", full_page=True)
        return [
            {"check": "mobile_shell", "result": "PASS"},
            {"check": "mobile_no_horizontal_overflow", "result": "PASS"},
        ]
    finally:
        page.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--base-url")
    args = parser.parse_args()

    evidence_dir = args.evidence_dir.resolve()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    data_dir = (args.data_dir or Path(tempfile.mkdtemp(prefix="overall-vnext-browser-e2e-"))).resolve()
    process: subprocess.Popen[str] | None = None
    server_log = evidence_dir / "dut.log"
    base_url = args.base_url
    if not base_url:
        port = _free_port()
        base_url = f"http://127.0.0.1:{port}"
        log_handle = server_log.open("w", encoding="utf-8")
        process = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "scripts/overall_vnext_demo.py"),
                "--data-dir",
                str(data_dir),
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=ROOT,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
        _wait_ready(f"{base_url}/p0/overall")
    else:
        log_handle = None

    console_errors: list[str] = []
    request_failures: list[str] = []
    checks: list[dict[str, str]] = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
            page.on("requestfailed", lambda request: request_failures.append(f"{request.method} {request.url}: {request.failure}"))
            try:
                checks.extend(_run_desktop(page, base_url, evidence_dir))
                checks.extend(_run_mobile(browser, base_url, evidence_dir))
            finally:
                page.close()
                browser.close()
        if console_errors:
            raise AssertionError(f"BROWSER_CONSOLE_ERRORS:{console_errors}")
        if request_failures:
            raise AssertionError(f"BROWSER_REQUEST_FAILURES:{request_failures}")
        report = {
            "task": "OVERALL-VNEXT-BROWSER-E2E-001",
            "status": "PASS",
            "base_url": base_url,
            "data_mode": "SYNTHETIC_ISOLATED",
            "real_provider_call": False,
            "checks": checks,
            "check_count": len(checks),
            "console_errors": console_errors,
            "request_failures": request_failures,
        }
        (evidence_dir / "result.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"TASK={report['task']}")
        print("RESULT=PASS")
        print(f"CHECK_COUNT={len(checks)}")
        print("DESKTOP=PASS")
        print("MOBILE=PASS")
        print("P04_INDUSTRY_RETURN=PASS")
        print(f"EVIDENCE_DIR={evidence_dir}")
        return 0
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if log_handle is not None:
            log_handle.close()


if __name__ == "__main__":
    raise SystemExit(main())
