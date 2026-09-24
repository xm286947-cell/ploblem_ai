from __future__ import annotations

import os
import sys
import threading
import time
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import urlopen

import openpyxl
import uvicorn
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.web.p0_app import create_p0_app



def _fixture_xlsx(path: Path) -> None:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "分类"
    sheet.append(["说明"])
    sheet.append(["编码", "一级", "二级", "三级", "备注"])
    sheet.append(["K-USB", "连接器", "特殊连接器", "USB", "接口"])
    sheet.append(["K-HDMI", "连接器", "特殊连接器", "HDMI", "视频"])
    workbook.save(path)
    workbook.close()


def _wait_server(url: str, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urlopen(url, timeout=1.0) as response:
                if response.status < 500:
                    return
        except Exception:
            time.sleep(0.15)
    raise RuntimeError("P07 screenshot server did not become ready")


def _click_all_confirm(page) -> None:
    deadline = time.time() + 15.0
    while time.time() < deadline:
        buttons = page.locator(".hc-confirm-change")
        if buttons.count() == 0:
            return
        buttons.first.click()
        page.wait_for_timeout(180)
    raise RuntimeError("Timed out confirming ChangeSet")


def main() -> int:
    output = Path(os.environ.get("HC_TREE_SCREENSHOT_DIR", "artifacts/hc_tree_m3b"))
    output.mkdir(parents=True, exist_ok=True)

    with TemporaryDirectory(prefix="hc-tree-m3b-") as work:
        workdir = Path(work)
        p0_db = workdir / "quality_capability_p0.db"
        hardware_db = workdir / "hardware_case_mvp.db"
        upload_dir = workdir / "tree_uploads"
        fixture = workdir / "circuit.xlsx"

        initializer = P0Initializer(
            manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
            plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
        )
        initializer.initialize(p0_db)
        _fixture_xlsx(fixture)

        app = create_p0_app(
            p0_db,
            stage_runner=object(),
            hardware_case_db_path=hardware_db,
            hardware_tree_upload_dir=upload_dir,
        )
        config = uvicorn.Config(app, host="127.0.0.1", port=8877, log_level="warning")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        _wait_server("http://127.0.0.1:8877/api/v2/initialization/status")

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
                page.goto("http://127.0.0.1:8877/p0/hardware-cases/base-data", wait_until="networkidle")
                page.screenshot(path=str(output / "01_p07_home.png"), full_page=True)

                page.locator('[data-start-import="CIRCUIT_FEATURE"]').click()
                page.fill("#hc-operator", "m3b-visual-gate")
                page.set_input_files("#hc-file", str(fixture))
                page.locator('#hc-upload-form button[type="submit"]').click()
                page.locator("#hc-step-2").wait_for(state="visible")

                page.fill("#hc-header-row", "2")
                for column in ["编码", "一级", "二级", "三级", "备注"]:
                    page.fill("#hc-column-name", column)
                    page.click("#hc-add-column")
                rows = page.locator(".hc-mapping-row")
                rows.nth(0).locator(".hc-map-role").select_option("BUSINESS_KEY")
                page.locator(".hc-mapping-row").nth(4).locator(".hc-map-role").select_option("METADATA")

                page.click("#hc-run-analysis")
                page.locator("#hc-step-3").wait_for(state="visible")
                page.screenshot(path=str(output / "02_preview_validation.png"), full_page=True)

                page.click("#hc-to-diff")
                page.locator("#hc-step-4").wait_for(state="visible")
                page.screenshot(path=str(output / "03_change_diff.png"), full_page=True)

                _click_all_confirm(page)
                page.locator("#hc-ready-apply").wait_for(state="visible")
                page.screenshot(path=str(output / "04_change_reviewed.png"), full_page=True)
                page.click("#hc-ready-apply")
                page.locator("#hc-step-5").wait_for(state="visible")
                page.screenshot(path=str(output / "05_confirm_apply.png"), full_page=True)

                page.click("#hc-confirm-apply")
                page.locator(".hc-result-success").wait_for(state="visible")
                page.screenshot(path=str(output / "06_apply_success.png"), full_page=True)

                page.click("#hc-view-import-history")
                page.locator("#hc-step-6").wait_for(state="visible")
                page.screenshot(path=str(output / "07_version_history.png"), full_page=True)
                browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=5)

    print(f"HC-TREE-M3B screenshots written to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
