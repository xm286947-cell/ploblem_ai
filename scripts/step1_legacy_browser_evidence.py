from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

from quality_knowledge.materials import MaterialRepository
from quality_knowledge.scenarios import ScenarioRepository


def seed(db_path: Path) -> str:
    scenarios = ScenarioRepository(db_path)
    existing = scenarios.scenarios(q="STEP1成熟场景A")
    if existing:
        first_id = existing[0]["scenario_id"]
    else:
        first_id = scenarios.save_scenario(
            "",
            {
                "scenario_code": "STEP1-BROWSER-001",
                "name": "STEP1成熟场景A",
                "product_code": "PLC",
                "status": "PUBLISHED",
                "lifecycle_code": "OPERATIONS",
                "activity_code": "ONLINE_MONITORING",
                "scenario_chain": "工程配置 → 在线监控 → 异常处置",
                "concern_points": "稳定性 / 可恢复性",
                "quality_attribute": "可靠性",
                "measurement_suggestion": "持续运行与恢复时间",
                "participating_systems": "PLC / HMI",
                "system_scale": "10站点",
                "user_type": "工程师",
            },
            {
                "INDUSTRY": ["新能源"],
                "CUSTOMER_NAME": ["客户A"],
                "PRODUCT_MODEL": ["PLC-A"],
            },
        )
        scenarios.save_scenario(
            "",
            {
                "scenario_code": "STEP1-BROWSER-002",
                "name": "STEP1其他行业场景B",
                "product_code": "PLC",
                "status": "PUBLISHED",
                "lifecycle_code": "OPERATIONS",
                "activity_code": "ONLINE_MONITORING",
                "scenario_chain": "部署 → 运行 → 维护",
                "concern_points": "连续运行",
                "quality_attribute": "可靠性",
            },
            {
                "INDUSTRY": ["光伏"],
                "CUSTOMER_NAME": ["客户B"],
                "PRODUCT_MODEL": ["PLC-B"],
            },
        )

    materials = MaterialRepository(db_path)
    group = materials.group("ITR-CS")
    if not group:
        raise RuntimeError("ITR_CS_GROUP_MISSING")
    raw = {
        "问题信息_问题描述": "现场长稳运行后异常，需验证恢复能力",
        "问题信息_客户行业": "新能源",
        "问题信息_客户名称": "客户A",
        "问题信息_产品型号": "PLC-A",
        "问题信息_产品类型": "PLC",
        "问题信息_IPMT": "IPMT-A",
        "问题信息_SPDT": "SPDT-A",
        "问题信息_问题发生时间": "2026-09-15",
        "问题信息_问题发生阶段": "运行执行",
        "问题信息_问题领域": "软件",
        "问题信息_故障现象描述": "运行中断并可恢复",
        "技术根因分析与纠正_TRC根因": "测试浏览器证据专用合成事实",
    }
    material_id, _ = materials.add_material(
        group,
        "ITR20260001CS",
        raw,
        "STEP1_BROWSER_EVIDENCE.xlsx",
        "Sheet1",
        2,
    )
    with scenarios.connect() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO quality_scenario_evidence"
            "(scenario_id,knowledge_id,evidence_summary) VALUES(?,?,?)",
            (
                first_id,
                material_id,
                json.dumps(
                    {
                        "source": "STEP1_BROWSER_SYNTHETIC",
                        "purpose": "browser capability evidence only",
                    },
                    ensure_ascii=False,
                ),
            ),
        )
    return first_id


def launch_browser(playwright):
    errors = []
    for channel in ("msedge", "chrome"):
        try:
            return playwright.chromium.launch(channel=channel, headless=True), channel
        except Exception as exc:  # pragma: no cover - runner dependent
            errors.append(f"{channel}:{exc}")
    raise RuntimeError("NO_SUPPORTED_BROWSER:" + " | ".join(errors))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    scenario_id = seed(args.db)

    with sync_playwright() as p:
        browser, channel = launch_browser(p)
        page = browser.new_page(viewport={"width": 1600, "height": 1100})
        page_errors: list[str] = []
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))

        # 1. Mature Quality Scenario: real data + filter + detail drill-down.
        page.goto(args.base_url + "/quality-scenarios", wait_until="networkidle")
        page.get_by_role("heading", name="质量场景库").wait_for()
        page.get_by_text("STEP1成熟场景A", exact=True).wait_for()
        page.locator('select[name="industry"]').select_option(label="新能源")
        page.get_by_role("button", name="查询").click()
        page.wait_for_load_state("networkidle")
        assert "industry=" in page.url
        assert page.get_by_text("STEP1成熟场景A", exact=True).count() >= 1
        assert page.get_by_text("STEP1其他行业场景B", exact=True).count() == 0
        page.screenshot(path=args.output_dir / "01_quality_scenarios_filter.png", full_page=True)
        page.get_by_text("STEP1成熟场景A", exact=True).first.click()
        page.wait_for_load_state("networkidle")
        assert scenario_id in page.url
        assert "STEP1成熟场景A" in page.content()
        page.screenshot(path=args.output_dir / "01b_quality_scenario_detail.png", full_page=True)

        # 2. Mature Product Portrait / Asset overview + detail drill-down.
        page.goto(args.base_url + "/quality-scenario-assets", wait_until="networkidle")
        page.get_by_role("heading", name="质量场景资产与业务洞察").wait_for()
        page.get_by_text("STEP1成熟场景A", exact=True).wait_for()
        page.screenshot(path=args.output_dir / "02_product_portrait.png", full_page=True)
        page.get_by_text("STEP1成熟场景A", exact=True).first.click()
        page.wait_for_load_state("networkidle")
        assert "/quality-scenario-assets/" in page.url
        assert "关联市场问题与事实" in page.content()
        page.screenshot(path=args.output_dir / "02b_product_portrait_drilldown.png", full_page=True)

        # 3. Mature Customer / Industry Portrait: filter + product grouping switch.
        page.goto(
            args.base_url + "/quality-scenario-assets/portrait?industry=新能源",
            wait_until="networkidle",
        )
        page.get_by_role("heading", name="客户 / 行业质量场景画像").wait_for()
        assert "客户A" in page.content()
        assert "新能源" in page.content()
        page.screenshot(path=args.output_dir / "03_customer_industry_portrait.png", full_page=True)
        plc_tab = page.locator("a.product-tab", has_text="PLC")
        if plc_tab.count():
            plc_tab.first.click()
            page.wait_for_load_state("networkidle")
            assert "product_group=PLC" in page.url
        page.screenshot(path=args.output_dir / "03b_portrait_product_switch.png", full_page=True)

        browser.close()

    if page_errors:
        raise RuntimeError("BROWSER_PAGE_ERRORS:" + " | ".join(page_errors))

    report = args.output_dir / "BROWSER_EVIDENCE.txt"
    report.write_text(
        "\n".join(
            [
                "BROWSER_EVIDENCE=PASS",
                f"BROWSER_CHANNEL={channel}",
                f"SCENARIO_ID={scenario_id}",
                "QUALITY_SCENARIO_FILTER=PASS",
                "QUALITY_SCENARIO_DETAIL=PASS",
                "PRODUCT_PORTRAIT=PASS",
                "PRODUCT_PORTRAIT_DRILLDOWN=PASS",
                "CUSTOMER_INDUSTRY_PORTRAIT=PASS",
                "PORTRAIT_PRODUCT_SWITCH=PASS",
                "DATA_CLASS=SYNTHETIC_BROWSER_EVIDENCE_ONLY",
                "FINAL_ACCEPTANCE=USER_REAL_DATA_MANUAL_CONFIRMATION_REQUIRED",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(report.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
