#!/usr/bin/env python3
from __future__ import annotations

import json
import os
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


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = Path(os.environ.get("QS_SEMANTIC_EVIDENCE_DIR", ROOT / "validation" / "mac_semantic_e2e")).resolve()

EXPECTATIONS = {
    "G1_COMPLETE": {
        "required_groups": [
            ["掉电", "断电", "异常掉电"],
            ["恢复", "上电", "重启"],
            ["参数", "数据", "持久", "一致性", "完整性"],
        ],
        "forbidden": ["通信链路", "日志占满", "CPU争用"],
        "product_intent": "异常掉电后关键业务参数/数据保持与恢复一致性，可形成可执行恢复验证场景。",
    },
    "G2_NO_MISSED_TEST": {
        "required_groups": [
            ["通信", "链路", "网络"],
            ["抖动", "波动", "不稳定"],
            ["重连", "会话", "超时"],
        ],
        "forbidden": ["异常掉电", "掉电恢复", "日志占满"],
        "product_intent": "通信链路波动后重连/会话超时场景；漏测来源必须保持 MISSING，不允许伪造。",
    },
    "G4_DUPLICATE_GENERATE": {
        "required_groups": [
            ["日志"],
            ["存储", "空间", "容量", "占满"],
            ["轮转", "限流", "阈值"],
        ],
        "forbidden": ["异常掉电", "重连状态机", "CPU争用"],
        "product_intent": "高频日志写入导致存储空间耗尽，形成容量/轮转/限流边界测试场景。",
    },
    "G5_SOURCE_REVISION": {
        "required_groups": [
            ["调度", "周期", "超时"],
            ["CPU", "资源", "争用"],
            ["长时间", "长稳", "后台", "预算", "优先级"],
        ],
        "forbidden": ["异常掉电", "日志占满", "通信链路波动"],
        "product_intent": "长稳运行下后台任务与控制周期资源争用，形成调度预算/优先级/周期超时场景。",
    },
}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_http(url: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            with urlopen(url, timeout=0.8) as response:
                if response.status == 200:
                    return
        except Exception as error:
            last = error
            time.sleep(0.15)
    raise RuntimeError(f"SERVER_NOT_READY:{url}:{last}")


@contextmanager
def _running_app(app, port: int):
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    _wait_http(f"http://127.0.0.1:{port}/issues")
    try:
        yield
    finally:
        server.should_exit = True
        thread.join(timeout=8)


def _case(manifest: dict, case_id: str) -> dict:
    return next(item for item in manifest["cases"] if item["case_id"] == case_id)


def _prepare_case(page, base: str, material_id: str, reason: str):
    page.goto(base + "/software-assessment", wait_until="networkidle")
    box = page.locator(f'[data-assessment-select][value="{material_id}"]')
    box.check()
    page.locator("[data-trigger-source]").select_option("HIGH_PERCEPTION")
    page.locator("[data-trigger-reason]").fill(reason)
    page.locator("[data-preview]").click()


def _scenario_id_from_actions(page) -> str:
    href = page.locator(
        '.sa-qsv1-actions a[href^="/quality-scenarios/workbench?scenario_id="]'
    ).first.get_attribute("href")
    if not href:
        raise AssertionError("SCENARIO_ACTION_LINK_MISSING")
    values = parse_qs(urlparse(href).query)
    return values["scenario_id"][0]


def _generate(page, base: str, material_id: str, reason: str, expected_preview: str = "READY") -> str:
    _prepare_case(page, base, material_id, reason)
    page.locator(f'[data-state="{expected_preview}"]').wait_for(timeout=30000)
    page.locator("[data-generate]").click()
    page.locator('[data-state="CANDIDATE_CREATED"]').wait_for(timeout=180000)
    return _scenario_id_from_actions(page)


def _publish(page, base: str, scenario_id: str, case_id: str) -> None:
    page.goto(base + "/quality-scenarios/workbench?scenario_id=" + scenario_id, wait_until="networkidle")
    page.locator("[data-scenario-id]").wait_for(timeout=30000)
    page.locator("[data-quality-actor]").fill("Semantic E2E Quality")
    page.locator("[data-technical-actor]").fill("Semantic E2E Technical")
    page.locator("[data-confirmation-note]").fill(f"{case_id} macOS real-provider semantic E2E")
    page.locator('[data-action="confirm"]').click()
    page.locator("[data-scenario-status]").filter(has_text="Confirmed").wait_for(timeout=30000)
    page.locator('[data-action="publish"]').click()
    page.locator("[data-scenario-status]").filter(has_text="Published").wait_for(timeout=30000)


def _get_json(page, url: str) -> dict:
    response = page.request.get(url)
    if not response.ok:
        raise AssertionError(f"GET_FAILED:{url}:{response.status}")
    return response.json()


def _scenario_text(scenario: dict) -> str:
    fields = [
        "scenario_name",
        "scenario_description",
        "business_goal",
        "quality_concern_name",
        "quality_concern_code",
        "trigger_condition",
        "expected_result",
        "applicability_scope",
    ]
    return "\n".join(str(scenario.get(name) or "") for name in fields)


def _evaluate(case_id: str, scenario: dict, trace: dict) -> dict:
    expectation = EXPECTATIONS[case_id]
    text = _scenario_text(scenario)
    group_results = []
    for alternatives in expectation["required_groups"]:
        matched = [term for term in alternatives if term in text]
        group_results.append({"alternatives": alternatives, "matched": matched, "pass": bool(matched)})
    forbidden_hits = [term for term in expectation["forbidden"] if term in text]
    required_fields = {
        name: bool(str(scenario.get(name) or "").strip())
        for name in ("scenario_name", "scenario_description", "trigger_condition", "expected_result")
    }
    integrity = ((trace.get("integrity") or {}).get("status") or "").upper()
    group_pass_count = sum(1 for item in group_results if item["pass"])
    semantic_smoke = group_pass_count >= 2 and not forbidden_hits
    structural = all(required_fields.values())
    evidence_pass = integrity == "PASS"
    return {
        "product_intent": expectation["product_intent"],
        "required_group_results": group_results,
        "required_group_pass_count": group_pass_count,
        "forbidden_hits": forbidden_hits,
        "required_fields": required_fields,
        "evidence_integrity": integrity,
        "semantic_smoke": "PASS" if semantic_smoke else "REVIEW_REQUIRED",
        "structural_gate": "PASS" if structural else "FAIL",
        "evidence_gate": "PASS" if evidence_pass else "FAIL",
        "human_review_required": True,
    }


def _markdown(report: dict) -> str:
    lines = [
        "# Quality Scenario macOS Real-Provider Semantic E2E",
        "",
        f"- Source SHA: {report['product_source_sha']}",
        f"- Provider: {report['provider']}",
        f"- Overall hard gate: {report['overall_hard_gate']}",
        "",
        "This report is a product-semantic acceptance aid. Automatic checks only catch structural/evidence defects and obvious cross-case contamination. Human product review remains authoritative.",
        "",
    ]
    for case_id in ("G1_COMPLETE", "G2_NO_MISSED_TEST", "G3_CONFLICT", "G4_DUPLICATE_GENERATE", "G5_SOURCE_REVISION"):
        item = report["cases"][case_id]
        lines += [f"## {case_id}", ""]
        if case_id == "G3_CONFLICT":
            lines += [
                f"- Preview state: {item.get('preview_state')}",
                f"- Generate disabled: {item.get('generate_disabled')}",
                "- Product intent: conflicting Resolution must fail closed; no scenario is generated.",
                "",
            ]
            continue
        scenario = item["scenario"]
        evaluation = item["evaluation"]
        lines += [
            f"- Scenario ID: {scenario.get('scenario_id')}",
            f"- Status: {scenario.get('status')}",
            f"- Semantic smoke: {evaluation['semantic_smoke']}",
            f"- Evidence integrity: {evaluation['evidence_integrity']}",
            f"- Product intent: {evaluation['product_intent']}",
            "",
            "### Actual scenario",
            "",
            f"- Name: {scenario.get('scenario_name')}",
            f"- Description: {scenario.get('scenario_description')}",
            f"- Business goal: {scenario.get('business_goal')}",
            f"- Quality concern: {scenario.get('quality_concern_name') or scenario.get('quality_concern_code')}",
            f"- Trigger: {scenario.get('trigger_condition')}",
            f"- Expected result: {scenario.get('expected_result')}",
            f"- Scope: {scenario.get('applicability_scope')}",
            "",
            "### Automatic semantic signals",
            "",
        ]
        for group in evaluation["required_group_results"]:
            lines.append(f"- {'PASS' if group['pass'] else 'MISS'}: {' / '.join(group['alternatives'])} -> {', '.join(group['matched']) or 'no match'}")
        lines += [
            f"- Forbidden hits: {', '.join(evaluation['forbidden_hits']) or 'none'}",
            "",
            "### Human product review",
            "",
            "- [ ] Source fidelity",
            "- [ ] Reusable scenario abstraction",
            "- [ ] Trigger/precondition quality",
            "- [ ] Failure/quality-risk meaning",
            "- [ ] Expected-result/actionability",
            "- [ ] Testability",
            "- [ ] Evidence does not overclaim",
            "- [ ] Overall scenario is one we want to keep",
            "",
        ]
    return "\n".join(lines) + "\n"


def main() -> int:
    if not os.environ.get("DASHSCOPE_BASE_URL"):
        raise SystemExit("DASHSCOPE_BASE_URL_REQUIRED")
    if not os.environ.get("DASHSCOPE_API_KEY"):
        raise SystemExit("DASHSCOPE_API_KEY_REQUIRED")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise SystemExit("PLAYWRIGHT_REQUIRED") from error

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    source_db = EVIDENCE_DIR / "quality_scenario_semantic_source.db"
    qsv1_db = EVIDENCE_DIR / "quality_scenario_semantic_result.db"
    for path in (source_db, qsv1_db):
        if path.exists():
            path.unlink()
    manifest = build_fixture(source_db)

    os.environ["QUALITY_SCENARIO_V1_DB_PATH"] = str(qsv1_db)
    os.environ["REVERSE_QUALITY_MODEL_CONFIG"] = str(ROOT / "config/runtime/model.yaml")

    app_port = _free_port()
    report = {
        "contract": "quality-scenario-mac-real-provider-semantic-e2e/v1",
        "product_source_sha": os.environ.get("GITHUB_SHA") or "LOCAL",
        "provider": "qwen_prod/openai_compatible/real",
        "source_fixture_contract": manifest.get("contract"),
        "direct_qsv1_candidate_write": False,
        "direct_qsv1_publish_write": False,
        "cases": {},
    }

    with _running_app(create_app(source_db), app_port):
        base = f"http://127.0.0.1:{app_port}"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1600, "height": 1200})

            for case_id in ("G1_COMPLETE", "G2_NO_MISSED_TEST", "G4_DUPLICATE_GENERATE"):
                case = _case(manifest, case_id)
                scenario_id = _generate(
                    page,
                    base,
                    case["software_assessment_material_id"],
                    f"MAC_REAL_SEMANTIC_{case_id}",
                )
                scenario = _get_json(
                    page,
                    base + "/api/v2/quality-scenario-workflow/v1/quality-scenarios/" + scenario_id,
                )
                trace = _get_json(
                    page,
                    base + "/api/v2/quality-scenario-workflow/v1/quality-scenarios/" + scenario_id + "/traceability",
                )
                page.goto(base + "/quality-scenarios/library/" + scenario_id, wait_until="networkidle")
                page.screenshot(path=str(EVIDENCE_DIR / f"{case_id}.png"), full_page=True)
                report["cases"][case_id] = {
                    "scenario": scenario,
                    "trace": trace,
                    "evaluation": _evaluate(case_id, scenario, trace),
                }

            g3 = _case(manifest, "G3_CONFLICT")
            _prepare_case(page, base, g3["software_assessment_material_id"], "MAC_REAL_SEMANTIC_G3")
            page.locator('[data-state="INFORMATION_REQUIRED"]').wait_for(timeout=30000)
            g3_disabled = page.locator("[data-generate]").is_disabled()
            page.screenshot(path=str(EVIDENCE_DIR / "G3_CONFLICT.png"), full_page=True)
            report["cases"]["G3_CONFLICT"] = {
                "preview_state": "INFORMATION_REQUIRED",
                "generate_disabled": g3_disabled,
            }

            g5 = _case(manifest, "G5_SOURCE_REVISION")
            old_id = _generate(
                page,
                base,
                g5["software_assessment_material_id"],
                "MAC_REAL_SEMANTIC_G5",
            )
            old_scenario = _get_json(
                page,
                base + "/api/v2/quality-scenario-workflow/v1/quality-scenarios/" + old_id,
            )
            old_trace = _get_json(
                page,
                base + "/api/v2/quality-scenario-workflow/v1/quality-scenarios/" + old_id + "/traceability",
            )
            _publish(page, base, old_id, "G5_SOURCE_REVISION")
            advance_g5(source_db)

            _prepare_case(page, base, g5["software_assessment_material_id"], "MAC_REAL_SEMANTIC_G5")
            page.locator('[data-state="SOURCE_CHANGED_REANALYSIS_AVAILABLE"]').wait_for(timeout=30000)
            page.locator("[data-generate]").click()
            page.locator('[data-state="CANDIDATE_CREATED"]').wait_for(timeout=180000)
            new_id = _scenario_id_from_actions(page)
            new_scenario = _get_json(
                page,
                base + "/api/v2/quality-scenario-workflow/v1/quality-scenarios/" + new_id,
            )
            new_trace = _get_json(
                page,
                base + "/api/v2/quality-scenario-workflow/v1/quality-scenarios/" + new_id + "/traceability",
            )
            page.goto(base + "/quality-scenarios/library/" + new_id, wait_until="networkidle")
            page.screenshot(path=str(EVIDENCE_DIR / "G5_SOURCE_REVISION_NEW.png"), full_page=True)
            report["cases"]["G5_SOURCE_REVISION"] = {
                "old_scenario": old_scenario,
                "old_trace": old_trace,
                "scenario": new_scenario,
                "trace": new_trace,
                "old_scenario_id": old_id,
                "new_scenario_id": new_id,
                "lineage_changed": old_id != new_id,
                "evaluation": _evaluate("G5_SOURCE_REVISION", new_scenario, new_trace),
            }

            browser.close()

    hard_failures = []
    for case_id in ("G1_COMPLETE", "G2_NO_MISSED_TEST", "G4_DUPLICATE_GENERATE", "G5_SOURCE_REVISION"):
        evaluation = report["cases"][case_id]["evaluation"]
        if evaluation["structural_gate"] != "PASS":
            hard_failures.append(case_id + ":STRUCTURAL")
        if evaluation["evidence_gate"] != "PASS":
            hard_failures.append(case_id + ":EVIDENCE")
        if evaluation["forbidden_hits"]:
            hard_failures.append(case_id + ":CROSS_CASE_CONTAMINATION")
    if not report["cases"]["G3_CONFLICT"]["generate_disabled"]:
        hard_failures.append("G3:FAIL_CLOSED")
    if not report["cases"]["G5_SOURCE_REVISION"]["lineage_changed"]:
        hard_failures.append("G5:LINEAGE")

    report["hard_failures"] = hard_failures
    report["overall_hard_gate"] = "PASS" if not hard_failures else "FAIL"
    (EVIDENCE_DIR / "semantic_e2e_result.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (EVIDENCE_DIR / "SEMANTIC_ACCEPTANCE_REPORT.md").write_text(
        _markdown(report), encoding="utf-8"
    )

    print("TASK=QUALITY_SCENARIO_MAC_SEMANTIC_E2E_001")
    print(f"OVERALL_HARD_GATE={report['overall_hard_gate']}")
    print("HUMAN_PRODUCT_REVIEW=REQUIRED")
    print(f"EVIDENCE_DIR={EVIDENCE_DIR}")
    for case_id in ("G1_COMPLETE", "G2_NO_MISSED_TEST", "G4_DUPLICATE_GENERATE", "G5_SOURCE_REVISION"):
        print(f"{case_id}_SEMANTIC_SMOKE={report['cases'][case_id]['evaluation']['semantic_smoke']}")
    print(f"G3_CONFLICT_FAIL_CLOSED={'PASS' if report['cases']['G3_CONFLICT']['generate_disabled'] else 'FAIL'}")
    return 0 if not hard_failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
