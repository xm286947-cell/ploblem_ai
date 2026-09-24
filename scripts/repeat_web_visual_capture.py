from __future__ import annotations

import argparse
import threading
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from playwright.sync_api import sync_playwright

from quality_knowledge.web.p0_pages import create_p0_insights_router


MODE = {"value": "success"}


def _candidate(detail_status: str = "SUCCESS"):
    return {
        "case_id": "HCASE-DEMO-001",
        "title": "掉电恢复后启动失败",
        "historical_phenomenon": "掉电恢复后启动失败，配置文件无法加载",
        "retrieval_score": 0.87,
        "rank": 1,
        "why_relevant": [
            {"type": "RETRIEVAL_REASON", "text": "问题均发生于掉电恢复场景"},
            {"type": "RETRIEVAL_REASON", "text": "历史案例存在相同数据保存路径"},
            {"type": "MATCHED_FIELD", "field": "problem", "text": "当前现象与历史启动失败特征一致"},
        ],
        "explanation_status": "EXPLAINED",
        "explanation_message": None,
        "root_causes": ["掉电窗口存在未完成写入，恢复时读取到不完整数据"],
        "measures": ["增加原子保存与启动恢复校验"],
        "verification": "连续 100 次掉电恢复验证通过",
        "evidence_refs": [{"source_type": "REPORT", "source_id": "ITR-H-001", "file_name": "复盘报告.pdf", "page": 7, "section": "Root Cause"}],
        "evidence": [{
            "evidence_id": "EVD-DEMO-001",
            "source_type": "REPORT",
            "source_id": "ITR-H-001",
            "file_name": "复盘报告.pdf",
            "page": 7,
            "section": "Root Cause",
            "raw_text": "掉电发生在配置写入窗口，启动时检测到文件尾部不完整。",
            "target_path": "root_cause",
            "url": None,
        }],
        "source_ref": "ITR-H-001",
        "source_refs": ["ITR-H-001"],
        "detail_status": detail_status,
        "detail_error": None if detail_status == "SUCCESS" else "EVIDENCE_PARTIAL",
        "case_status": "ACTIVE",
    }


def _result(status: str = "READY_FOR_REVIEW", *, decided: bool = False):
    candidates = []
    if status in {"READY_FOR_REVIEW", "INCOMPLETE"}:
        candidates = [_candidate("INCOMPLETE" if status == "INCOMPLETE" else "SUCCESS")]
    return {
        "contract_version": "repeat-result/v1",
        "query_id": "RQ-DEMO-001",
        "subject_ref": "ITR-DEMO-001",
        "correlation_id": "CORR-DEMO",
        "query_snapshot": {
            "itr_version": "V3",
            "include_missed_test": True,
            "missed_test_ref": "MISS-DEMO-001",
            "algorithm_version": "repeat-risk/v1",
        },
        "search_status": {
            "READY_FOR_REVIEW": "SUCCESS",
            "NO_CANDIDATES": "NO_CANDIDATES",
            "SEARCH_UNAVAILABLE": "SEARCH_UNAVAILABLE",
            "INCOMPLETE": "INCOMPLETE",
        }[status],
        "result_status": status,
        "search_error": "CASE_SERVICE_UNAVAILABLE" if status == "SEARCH_UNAVAILABLE" else None,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "warnings": [],
        "human_decision": {
            "decision": "REPEAT" if decided else "PENDING",
            "decided_by": "quality-owner" if decided else None,
            "reason": "历史问题、触发场景和保存路径一致，Evidence 已核对。" if decided else None,
            "decided_at": "2026-09-24T08:30:00+08:00" if decided else None,
        },
        "generated_at": "2026-09-24T08:20:00+08:00",
    }


def build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(create_p0_insights_router())

    @app.get("/api/v2/issues/K-DEMO")
    def issue():
        return {
            "issue": {
                "knowledge_id": "K-DEMO",
                "business_issue_id": "ITR-DEMO-001",
                "business_type": "PLC",
                "product_code": "PLC",
                "normalized_snapshot": {
                    "ISSUE_FACT": {
                        "business_issue_id": "ITR-DEMO-001",
                        "title": "掉电恢复后设备启动失败",
                        "description": "掉电发生后再次上电，设备因配置数据异常启动失败",
                        "product": "PLC",
                        "version": "V3.2",
                        "platform": "控制器",
                        "severity": "H",
                        "issue_type": "可靠性",
                    }
                },
                "raw_json": {"问题编号": "ITR-DEMO-001", "问题描述": "掉电恢复后设备启动失败"},
            },
            "analysis": None,
            "effective_analysis": None,
            "human_revisions": [],
        }

    @app.get("/api/v2/issues/K-DEMO/navigation")
    def navigation():
        return {"position": 1, "total": 1, "previous_id": None, "next_id": None}

    @app.get("/api/v2/issues/K-DEMO/repeat-risk")
    def repeat_state():
        return {
            "subject": {
                "itr_ref": "ITR-DEMO-001",
                "source": "ITR_RESOLUTION_WORKBENCH",
                "itr_snapshot": {
                    "itr_id": "ITR-DEMO-001",
                    "problem_description": "掉电发生后再次上电，设备因配置数据异常启动失败",
                    "product": "PLC",
                    "version": "V3.2",
                    "scene": "掉电恢复",
                    "itr_version": "V3",
                },
            },
            "optional_context": {
                "missed_test_available": True,
                "missed_test_ref": "MISS-DEMO-001",
            },
            "latest_query": None,
            "latest_result": None,
        }

    @app.post("/api/v2/issues/K-DEMO/repeat-risk/queries")
    def run_query():
        time.sleep(1.2)
        mode = MODE["value"]
        if mode == "empty":
            result = _result("NO_CANDIDATES")
        elif mode == "unavailable":
            result = _result("SEARCH_UNAVAILABLE")
        elif mode == "incomplete":
            result = _result("INCOMPLETE")
        else:
            result = _result("READY_FOR_REVIEW")
        return {"query": {"query_id": "RQ-DEMO-001"}, "search": {}, "result": result}

    @app.post("/api/v2/repeat-risk/queries/RQ-DEMO-001/decision")
    def decision():
        return _result("READY_FOR_REVIEW", decided=True)

    @app.post("/__mode/{mode}")
    def set_mode(mode: str):
        MODE["value"] = mode
        return {"mode": mode}

    @app.get("/api/v2/historical-cases")
    def cases():
        return {
            "contract_version": "historical-case/v1",
            "status_filter": "PUBLISHED",
            "total": 1,
            "items": [{
                "case_id": "HCASE-DEMO-001",
                "itr": "ITR-H-001",
                "title": "掉电恢复后启动失败",
                "product": "PLC",
                "version": None,
                "status": "PUBLISHED",
                "published_at": "2026-09-20T10:00:00+08:00",
                "case_version": "REV-12",
            }],
        }

    @app.get("/api/v2/historical-cases/HCASE-DEMO-001")
    def case_detail():
        return {
            "contract_version": "historical-case/v1",
            "case_id": "HCASE-DEMO-001",
            "itr": "ITR-H-001",
            "title": "掉电恢复后启动失败",
            "problem_description": "掉电发生后再次上电，配置文件损坏导致启动失败",
            "product": "PLC",
            "symptom": "启动阶段读取配置失败",
            "root_cause": "掉电窗口存在未完成写入",
            "solution": "增加原子保存与恢复校验",
            "verification_result": "100 次掉电恢复验证通过",
            "status": "ACTIVE",
            "publication_status": "PUBLISHED",
            "published_at": "2026-09-20T10:00:00+08:00",
            "case_version": "REV-12",
            "evidence": _candidate()["evidence"],
        }

    return app


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="artifacts/repeat-web-001")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    app = build_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=8765, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("visual fixture server failed to start")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        base = "http://127.0.0.1:8765"

        page.goto(base + "/p0/issues/K-DEMO", wait_until="networkidle")
        page.screenshot(path=str(out / "H01_query_before.png"), full_page=True)

        page.check("[data-missed-toggle]")
        page.click("[data-repeat-query]")
        page.wait_for_timeout(250)
        page.screenshot(path=str(out / "H02_running.png"), full_page=True)
        page.get_by_text("找到 1 个值得关注的历史案例").wait_for()
        page.screenshot(path=str(out / "H03_success.png"), full_page=True)

        page.click("[data-repeat-evidence='0']")
        page.screenshot(path=str(out / "H04_evidence_drawer.png"), full_page=True)
        page.click("[data-evidence-close]")

        page.select_option("[data-repeat-decision]", "REPEAT")
        page.fill("[data-repeat-decision-reason]", "人工核对历史 Evidence 后确认")
        page.click("[data-repeat-decision-save]")
        page.get_by_text("人工结论：REPEAT").wait_for()
        page.screenshot(path=str(out / "H05_decision.png"), full_page=True)

        for mode, filename, expected in [
            ("empty", "H06_empty.png", "本次查询未检索到符合当前条件的历史案例"),
            ("unavailable", "H07_search_unavailable.png", "历史案例检索当前不可用"),
            ("incomplete", "H08_incomplete.png", "查询已完成，但部分结果或 Evidence 不完整"),
        ]:
            page.request.post(base + "/__mode/" + mode)
            page.goto(base + "/p0/issues/K-DEMO", wait_until="networkidle")
            page.click("[data-repeat-query]")
            page.get_by_text(expected).wait_for()
            page.screenshot(path=str(out / filename), full_page=True)

        page.goto(base + "/p0/cases", wait_until="networkidle")
        page.get_by_text("掉电恢复后启动失败").wait_for()
        page.screenshot(path=str(out / "H09_case_list.png"), full_page=True)

        page.goto(base + "/p0/cases/HCASE-DEMO-001", wait_until="networkidle")
        page.get_by_text("01").wait_for()
        page.screenshot(path=str(out / "H10_case_detail.png"), full_page=True)
        browser.close()

    server.should_exit = True
    thread.join(timeout=5)
    print(f"SCREENSHOTS={out}")
    print("H01-H10=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
