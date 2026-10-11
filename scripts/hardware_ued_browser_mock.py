#!/usr/bin/env python3
"""Native Chromium Mock E2E against the *actual FastAPI hardware product pages*.

Only the browser's /api/v2/hardware-cases and /api/hardware-query/v1/search
requests are fulfilled from clearly marked synthetic fixtures. No provider,
database writes, reviewer approval, or Publish endpoints are exercised.
"""
from __future__ import annotations

import json
import re
import socket
import tempfile
import threading
import time
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn
from playwright.sync_api import sync_playwright
from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.web.p0_app import create_p0_app

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts" / "hardware_ued_browser_mock"
CASE = {
    "case_id": "A0152",
    "title": "MCU 串口异常（MOCK ONLY）",
    "case_status": "PUBLISHED",
    "evidence_health": "AVAILABLE",
    "product_context": {"product": "模拟产品"},
    "facts": {
        "symptom": {"confirmed_value": "串口通信偶发乱码（模拟）", "evidence_refs": ["EV-MOCK-001"]},
        "root_cause": {"confirmed_value": "供电波动（模拟）", "evidence_refs": ["EV-MOCK-001"]},
    },
    "mapping_paths": {"CIRCUIT_FEATURE": ["接口/串口"], "MATERIAL_DEVICE": ["MCU/控制器"]},
}
EVIDENCE = {
    "evidence_id": "EV-MOCK-001", "evidence_type": "WORD",
    "evidence_status": "AVAILABLE", "source_ref": "word:MOCK_A0152.docx",
    "excerpt_or_caption": "MCU 串口异常定位说明（模拟原文，不可当作工程事实）",
    "locator": {"section": "模拟章节", "block_id": "mock-p1"},
}
CANDIDATE = {
    "title": "模拟导入候选，不代表真实知识",
    "facts": {"symptom": "模拟现象", "root_cause": "模拟根因", "actions": "等待人工核对"},
    "evidence": [EVIDENCE], "mappings": [],
}


def app_for_isolated_data(root: Path):
    p0_db = root / "p0.db"
    P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    ).initialize(p0_db)
    return create_p0_app(
        p0_db,
        stage_runner=object(),
        hardware_case_db_path=root / "hardware.db",
        hardware_tree_upload_dir=root / "tree_uploads",
        hardware_case_source_root=root / "sources",
    )


def mock_response(route, items: list[dict], calls: list[dict]):
    request = route.request
    parsed = urlparse(request.url)
    path, method = parsed.path, request.method
    calls.append({"method": method, "path": path, "query": parsed.query})
    if path.startswith("/api/hardware-query/v1/search"):
        payload = {"results": [], "retrieval": {"query_agent": {"status": "FAST_PATH"}}}
    elif path.startswith("/api/v2/hardware-cases"):
        tail = path[len("/api/v2/hardware-cases"):].strip("/")
        if tail == "":
            payload = {"results": [CASE] if method == "GET" else [], "retrieval": {"mode": "SQLITE_FORMAL", "query_agent": {"status": "FAST_PATH"}}}
        elif tail == "intakes":
            if method == "POST":
                items[:] = [{
                    "intake_id": "I-MOCK-001", "filename": "A9999_UED_mock.docx",
                    "case_id": "A9999", "status": "UPLOADED", "source_ref": "MOCK_SOURCE_ONLY",
                }]
                payload = items[0]
            else:
                payload = {"items": items}
        elif tail.endswith("/process") and method == "POST":
            items[0]["status"] = "CANDIDATE_READY"
            payload = items[0]
        elif tail == "intakes/I-MOCK-001":
            payload = {**items[0], "candidate": CANDIDATE}
        elif tail.startswith("tree-imports/active-version"):
            payload = {"active_version": "MOCK_TREE_VERSION"}
        elif tail.startswith("trees/"):
            typ = tail.split("/")[-1]
            node = {"node_id": "N-MOCK-001", "name": "模拟接口", "description": "Mock tree node",
                    "path": ["模拟资产", "模拟接口"], "tree_type": typ}
            payload = {"nodes": [node]}
        elif tail.startswith("tree-nodes/"):
            payload = {"results": [CASE]}
        elif tail == "A0152":
            payload = CASE
        elif tail == "A0152/mappings":
            payload = {"mappings": []}
        elif tail == "A0152/evidence":
            payload = {"evidence": [EVIDENCE]}
        elif tail.endswith("/source-preview"):
            payload = {"preview_status": "AVAILABLE", "blocks": [
                {"block_type": "PARAGRAPH", "text": "这只是一段模拟 Word 原文，未读取任何正式文件。",
                 "matched": True, "section_path": ["Mock"], "source_locator": {"block_id": "mock-p1"}}
            ]}
        elif tail == "maintenance/anomalies":
            payload = {"total": 0}
        elif tail == "A9999":
            payload = {"case_id": "A9999", "title": CANDIDATE["title"],
                       "case_status": "CANDIDATE", "evidence_health": "AVAILABLE",
                       "facts": CANDIDATE["facts"], "product_context": {}}
        elif tail == "A9999/mappings":
            payload = {"mappings": []}
        elif tail == "A9999/evidence":
            payload = {"evidence": [EVIDENCE]}
        elif tail == "A9999/publish-gate":
            payload = {"allowed": False, "can_publish": False, "blocking_reasons": ["MOCK_ONLY"]}
        else:
            raise AssertionError("Unregistered Mock endpoint: " + method + " " + path)
    else:
        raise AssertionError("Unexpected mock route: " + path)
    route.fulfill(status=200, content_type="application/json; charset=utf-8", body=json.dumps(payload, ensure_ascii=False))


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    results, calls, errors = [], [], []
    with tempfile.TemporaryDirectory(prefix="hardware-ued-real-pages-mock-") as tmp:
        app = app_for_isolated_data(Path(tmp))
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, access_log=False, log_level="warning"))
        runner = threading.Thread(target=server.run, daemon=True)
        runner.start()
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.1)
        if not server.started:
            raise RuntimeError("Isolated real FastAPI server failed to start")
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
                context = browser.new_context(viewport={"width": 1366, "height": 768}, device_scale_factor=1, accept_downloads=True)
                page = context.new_page()
                page.on("pageerror", lambda exc: errors.append(str(exc)))
                items = []
                page.route(re.compile(r"/api/(?:v2/hardware-cases|hardware-query/v1/search)"), lambda route: mock_response(route, items, calls))
                base = f"http://127.0.0.1:{port}"
                def check(label, fn):
                    fn()
                    results.append({"test": label, "result": "PASS"})

                check("真实 FastAPI 三主入口导航", lambda: (
                    page.goto(base + "/p0/hardware-cases/search?q=MCU"),
                    page.locator("[data-ued-primary]").count() == 3 or (_ for _ in ()).throw(AssertionError("nav!=3")),
                    page.get_by_role("link", name="找知识", exact=True).is_visible() or (_ for _ in ()).throw(AssertionError("find missing")),
                ))
                page.get_by_text("MCU 串口异常（MOCK ONLY）").first.wait_for()
                results.append({"test": "实际案例搜索 JS + Mock API", "result": "PASS"})
                page.screenshot(path=str(out / "01_actual_search_mock_api.png"), full_page=True)
                # No new search engine; the existing two read-only screens share the user's query.
                check("搜索词从案例视图传递到正式知识视图", lambda: (
                    page.locator("[data-hc-ued-forward-query]").click(),
                    page.wait_for_url(re.compile(r"/p0/hardware-cases/knowledge\?q=MCU")),
                    page.locator("[data-knowledge-text]").input_value() == "MCU"
                    or (_ for _ in ()).throw(AssertionError("formal query not retained")),
                ))
                # Formal search: HTTP failures are not empty hits, and retry uses the same URL/data.
                page.route(
                    re.compile(r"/api/hardware-query/v1/search"),
                    lambda route: route.fulfill(
                        status=500, content_type="application/json",
                        body='{"detail":"MOCK_FORMAL_TEMPORARY_FAILURE"}'
                    ),
                    times=1,
                )
                check("正式知识服务故障明确提示重试而非空结果", lambda: (
                    page.locator("[data-knowledge-form] button[type=submit]").click(),
                    page.locator("[data-knowledge-unavailable-title]").get_by_text("正式知识检索暂时失败").wait_for(),
                    page.locator("[data-knowledge-production]").is_hidden()
                    or (_ for _ in ()).throw(AssertionError("Transient 500 linked to knowledge production")),
                ))
                check("正式知识检索故障恢复后可重试", lambda: (
                    page.locator("[data-knowledge-retry]").click(),
                    page.locator("[data-knowledge-unavailable]").wait_for(state="hidden"),
                    page.locator("[data-knowledge-summary]").get_by_text(re.compile(r"0 条正式知识")).wait_for(),
                ))
                page.route(
                    re.compile(r"/api/hardware-query/v1/search"),
                    lambda route: route.fulfill(
                        status=503, content_type="application/json",
                        body='{"detail":"MOCK_FORMAL_INDEX_UNAVAILABLE"}'
                    ),
                    times=1,
                )
                check("正式知识 503 索引提示与普通故障区分", lambda: (
                    page.locator("[data-knowledge-form] button[type=submit]").click(),
                    page.locator("[data-knowledge-unavailable-title]").get_by_text("正式知识检索数据尚未生成").wait_for(),
                    page.locator("[data-knowledge-production]").is_visible()
                    or (_ for _ in ()).throw(AssertionError("Index 503 missing original maintenance link")),
                    page.locator("[data-knowledge-retry]").click(),
                    page.locator("[data-knowledge-unavailable]").wait_for(state="hidden"),
                ))
                check("正式知识搜索词写入 URL 和跨来源链接", lambda: (
                    page.locator("[data-knowledge-text]").fill("单片机"),
                    page.locator("[data-knowledge-form] button[type=submit]").click(),
                    page.wait_for_url(re.compile(r"q=%E5%8D%95%E7%89%87%E6%9C%BA")),
                    "q=%E5%8D%95%E7%89%87%E6%9C%BA" in page.locator("[data-hc-ued-back-query]").get_attribute("href")
                    or (_ for _ in ()).throw(AssertionError("back link lost formal query")),
                ))
                # Simulate two actual overlapping browser fetches: old slow response must not replace new.
                page.evaluate("""() => {
                    const original = window.fetch.bind(window);
                    window.fetch = (input, options) => {
                        const url = String(input);
                        if (url.includes('/api/hardware-query/v1/search')) {
                            const term = new URL(url, location.origin).searchParams.get('text');
                            if (term === '慢查询' || term === '快查询') {
                                const slow = term === '慢查询';
                                return new Promise(resolve => setTimeout(() => resolve(new Response(
                                    JSON.stringify({ results: [{
                                        knowledge_id: slow ? 'OLD-MOCK' : 'NEW-MOCK',
                                        business_case_id: 'A-MOCK', title: slow ? '旧结果不应出现' : '新结果应当保留',
                                        evidence_refs: [], match_score: 1
                                    }], retrieval: { query_agent: { status: 'FAST_PATH' } } }),
                                    { status: 200, headers: { 'Content-Type': 'application/json' } }
                                )), slow ? 350 : 20));
                            }
                        }
                        return original(input, options);
                    };
                    const form = document.querySelector('[data-knowledge-form]');
                    const field = document.querySelector('[data-knowledge-text]');
                    field.value = '慢查询';
                    form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
                    field.value = '快查询';
                    form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
                }""")
                page.get_by_text("新结果应当保留").first.wait_for()
                page.wait_for_timeout(450)
                check("正式知识慢旧请求不能覆盖新查询", lambda: (
                    page.get_by_text("新结果应当保留").first.is_visible()
                    or (_ for _ in ()).throw(AssertionError("new result lost")),
                    page.get_by_text("旧结果不应出现").count() == 0
                    or (_ for _ in ()).throw(AssertionError("stale result overwrote current query")),
                ))
                page.locator("[data-knowledge-text]").fill("MCU")
                page.locator("[data-knowledge-form] button[type=submit]").click()
                page.wait_for_url(re.compile(r"/p0/hardware-cases/knowledge\?q=MCU"))
                check("正式知识回案例视图保留检索词", lambda: (
                    page.locator("[data-hc-ued-back-query]").click(),
                    page.wait_for_url(re.compile(r"/p0/hardware-cases/search\?q=MCU")),
                    page.locator("[data-search-query]").input_value() == "MCU"
                    or (_ for _ in ()).throw(AssertionError("case query not retained")),
                ))
                check("手动更改搜索词会同步 URL", lambda: (
                    page.locator("[data-search-query]").fill("模拟量"),
                    page.locator("[data-search-form] button").click(),
                    page.wait_for_url(re.compile(r"q=%E6%A8%A1%E6%8B%9F%E9%87%8F")),
                ))
                # Search service failures must not be presented as genuine empty knowledge.
                page.route(
                    re.compile(r"/api/v2/hardware-cases(?:\?|$)"),
                    lambda route: route.fulfill(
                        status=503, content_type="application/json",
                        body='{"detail":"MOCK_SEARCH_UNAVAILABLE"}'
                    ),
                    times=1,
                )
                check("检索故障不是 0 条案例", lambda: (
                    page.locator("[data-search-form] button").click(),
                    page.locator("[data-search-results] [data-search-retry]").wait_for(),
                    "查询失败" in page.locator("[data-search-summary]").inner_text()
                    or (_ for _ in ()).throw(AssertionError("failure displayed as no hits")),
                ))
                check("恢复后可点击重新查询", lambda: (
                    page.locator("[data-search-retry]").click(),
                    page.get_by_text("MCU 串口异常（MOCK ONLY）").first.wait_for(),
                ))
                check("实际结果→案例详情", lambda: (
                    page.locator(".hc-case-item h3 a").first.click(),
                    page.wait_for_url(re.compile(r"/p0/hardware-cases/A0152")),
                    page.get_by_text("串口通信偶发乱码（模拟）").first.wait_for(),
                ))
                check("详情返回链接保留搜索词", lambda: (
                    "q=%E6%A8%A1%E6%8B%9F%E9%87%8F" in page.locator("[data-hc-ued-return-results]").get_attribute("href")
                    or (_ for _ in ()).throw(AssertionError("return link lost query")),
                ))
                check("实际案例 Evidence 抽屉→Mock 来源", lambda: (
                    page.locator("[data-evidence-id]").first.click(),
                    page.get_by_text("这只是一段模拟 Word 原文").first.wait_for(),
                ))
                page.screenshot(path=str(out / "02_actual_evidence_mock_api.png"), full_page=True)
                check("实际来源抽屉可关闭", lambda: (
                    page.locator("[data-close-evidence]").first.click(),
                    page.locator("[data-evidence-drawer]").wait_for(state="hidden"),
                ))
                check("实际双树切换", lambda: (
                    page.goto(base + "/p0/hardware-cases/tree"),
                    page.locator('[data-tree-type="MATERIAL_DEVICE"]').click(),
                    page.locator('[data-tree-type="MATERIAL_DEVICE"].active').wait_for(),
                ))
                page.screenshot(path=str(out / "03_actual_assets_mock_api.png"), full_page=True)
                check("实际 Word 表单→隔离 Mock Candidate", lambda: (
                    page.goto(base + "/p0/hardware-cases/intake"),
                    page.locator("[data-intake-files]").set_input_files({
                        "name": "A9999_UED_mock.docx",
                        "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        "buffer": b"MOCK ONLY - intercepted before backend",
                    }),
                    page.locator("[data-intake-upload] button").click(),
                    page.locator('[data-process-intake="I-MOCK-001"]').wait_for(),
                    page.locator('[data-process-intake="I-MOCK-001"]').click(),
                    page.locator("[data-intake-detail]").wait_for(state="visible"),
                    page.get_by_text("模拟导入候选，不代表真实知识").first.wait_for(),
                ))
                check("维护页中文候选状态与事实字段", lambda: (
                    "已生成候选" in page.locator("[data-intake-case-id]").inner_text()
                    or (_ for _ in ()).throw(AssertionError("raw candidate status exposed")),
                    page.locator("[data-intake-facts]").get_by_text("问题现象").wait_for(),
                    page.locator("[data-intake-facts]").get_by_text("根因").wait_for(),
                    page.locator("[data-intake-facts]").get_by_text("处置措施").wait_for(),
                    page.get_by_text("选择 Word 文件").first.wait_for(),
                ))
                page.screenshot(path=str(out / "04_actual_candidate_mock_api.png"), full_page=True)
                check("候选流转至实际审核页面", lambda: (
                    page.locator("[data-intake-review]").click(),
                    page.wait_for_url(re.compile(r"/p0/hardware-cases/A9999/review")),
                    page.locator("[data-hc-page='review']").wait_for(),
                ))
                page.screenshot(path=str(out / "05_actual_review_mock_api.png"), full_page=True)
                assert not any(call["path"].endswith("/publish") for call in calls), "No publish permitted"
                mobile = context.browser.new_page(viewport={"width": 390, "height": 844})
                mobile.route(re.compile(r"/api/(?:v2/hardware-cases|hardware-query/v1/search)"), lambda route: mock_response(route, items, calls))
                mobile.goto(base + "/p0/hardware-cases/search?q=MCU")
                mobile.locator("[data-ued-primary]").first.wait_for()
                check("390px 原生页面不横向溢出", lambda: (
                    mobile.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                    or (_ for _ in ()).throw(AssertionError("390px horizontal overflow")),
                ))
                mobile.screenshot(path=str(out / "06_actual_mobile_mock_api.png"), full_page=True)
                results.append({"test": "真实产品页面 390px 截图", "result": "PASS"})
                browser.close()
        finally:
            server.should_exit = True
            runner.join(timeout=15)
    report = {
        "scope": "Actual FastAPI product served on loopback, Chrome, browser-intercepted synthetic API responses",
        "tests": results, "javascript_errors": errors, "mock_api_calls": calls,
        "real_provider_calls": 0, "formal_data_mutations": 0, "publish_calls": 0,
    }
    (out / "RESULT.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if errors:
        raise AssertionError("Browser JS page errors: " + str(errors))
    print(json.dumps({"test_count": len(results), "result": "PASS", "screenshots": 6}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
