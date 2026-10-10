"""Real Linux Chromium browser DOM click gate from fresh-extract Major V1.1 ZIP (#614).

This exercises DOM events and HTTP roundtrips; it does NOT pretend to be
an authorized real LLM Provider run.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

from openpyxl import load_workbook
from playwright.sync_api import expect, sync_playwright
from pypdf import PdfWriter

ROOT = Path(__file__).resolve().parents[2]
URL = "http://127.0.0.1:18080/p0/major-production"
FIXTURE = ROOT / "tests/fixtures/major_v5/cases.xls"


def test_linux_full_candidate_browser_confirm_and_69_row_pages(tmp_path: Path) -> None:
    pdf = PdfWriter()
    pdf.add_blank_page(width=72, height=72)
    output = BytesIO()
    pdf.write(output)
    pdf_path = tmp_path / "ITR20269951-review.pdf"
    pdf_path.write_bytes(output.getvalue())
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(URL, wait_until="domcontentloaded")
            page.locator('[data-major-excel] input[name="file"]').set_input_files(str(FIXTURE))
            page.locator('[data-major-excel] input[name="materials"]').set_input_files(str(pdf_path))
            page.get_by_role("button", name="预检并查看 Mapping").click()
            preview = page.locator("[data-major-excel-preview]")
            expect(preview).to_contain_text("已保存的 Excel 批次", timeout=90000)
            expect(preview).to_contain_text("预检 1 行", timeout=30000)
            confirm = preview.get_by_role("button", name="确认导入当前批次")
            expect(confirm).to_be_enabled()
            confirm.click()
            expect(preview).to_contain_text("批次导入结果", timeout=90000)
            # The current batch must now be persisted; never ask a second POST.
            expect(preview).to_contain_text("实际成功导入 1 行")
            expect(preview.locator("[data-major-recovered-confirm]")).to_be_disabled()
            expect(preview.locator("[data-major-recovered-confirm]")).to_contain_text("已完成导入")
            pick = preview.get_by_role("button", name="选择此 Case 继续 AI 分析").first
            pick.click()
            expect(page.locator("[data-major-workflow]")).to_be_visible()
            expect(page.get_by_role("button", name="查看 AI 诊断")).to_be_visible()
            # Edge actually clicked Confirm. Verify no duplicate commit UI.
            assert page.locator('[data-major-recovered-confirm]').count() == 1

            # Separate real 69-row browser preview and last-page rendering.
            template_response = page.request.get(
                "http://127.0.0.1:18080/api/v2/major-production/excel/template"
            )
            assert template_response.ok
            wb = load_workbook(BytesIO(template_response.body()))
            ws = wb["重大问题"]
            headers = [str(cell.value or "") for cell in ws[1]]
            row2 = [cell.value for cell in ws[2]]
            for index in range(2, 71):
                values = dict(zip(headers, row2))
                values["IGR编号"] = f"IGR-2026-{index:04d}"
                values["ITR单号"] = f"ITR2027{index:04d}"
                values["问题描述"] = f"Linux UI 行号 {index}: 用于 69 行完整预览"
                for col, key in enumerate(headers, 1):
                    ws.cell(index, col).value = values.get(key)
            out = BytesIO()
            wb.save(out)
            rows_path = tmp_path / "edge-69.xlsx"
            rows_path.write_bytes(out.getvalue())
            page.locator('[data-major-excel] input[name="file"]').set_input_files(str(rows_path))
            page.locator('[data-major-excel] input[name="materials"]').set_input_files([])
            page.get_by_role("button", name="预检并查看 Mapping").click()
            expect(preview).to_contain_text("预检 69 行", timeout=90000)
            assert preview.locator("[data-paged-body] tr").count() == 20
            for _ in range(3):
                preview.get_by_role("button", name="下一页").first.click()
            expect(preview.locator("[data-paged-page]").first).to_contain_text("4 / 4")
            assert preview.locator("[data-paged-body] tr").count() == 9
            expect(preview).to_contain_text("Linux UI 行号 70")
        finally:
            browser.close()
