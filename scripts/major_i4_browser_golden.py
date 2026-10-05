"""Real Chrome G1-G6 acceptance for MAJOR-I4 Repeat typed semantics.

The UI is served by the production P0 page/router and its real Repeat Risk
services. Only the ITR subject and retrieval candidate are deterministic
fixtures. Run with the project's app dependencies plus Playwright installed.
"""
from __future__ import annotations

import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.p0.repository import P0Repository
from quality_knowledge.repeat_risk import RepeatQueryTraceRepository
from quality_knowledge.web.p0_app import create_p0_app
from quality_knowledge.web.repeat_risk_integration import RepeatWebFacade
from repositories import JsonArtifactRepository
from services.historical_case_contract import HistoricalCaseConsumerService


CASE_ID = "HCASE-I4-GOLDEN"
KNOWLEDGE_ID = "K-I4-GOLDEN"
MODES = {"value": "typed", "search_calls": 0}


def _install_case(artifacts: JsonArtifactRepository, mode: str) -> None:
    case = {
        "metadata": {
            "case_id": CASE_ID,
            "itr_id": "ITR-I4-HISTORY",
            "knowledge_revision": "I4-PUB-1",
        },
        "business_context": {"product": "PLC"},
        "problem": {
            "standard_description": "历史控制器掉电恢复后启动失败",
            "phenomenon": [{"value": "掉电恢复后启动失败"}],
        },
        "analysis": {"root_cause": [{"value": "GENERIC_FALLBACK_MUST_NOT_DISPLAY"}]},
        "solution": {
            "corrective_actions": [{"value": "LEGACY_ACTION_MUST_NOT_DISPLAY"}],
            "preventive_actions": [],
            "verification_result": "回归通过",
        },
        "knowledge": {"case_summary": "I4 typed semantic browser fixture"},
        "status": "ACTIVE",
    }
    sections: list[dict] = []

    if mode != "legacy":
        case["metadata"]["semantic_projection_contract"] = (
            "major-semantic-publish/v1" if mode != "unknown" else "major-semantic-publish/v99"
        )
        analysis = case["analysis"]
        analysis["trc"] = {}
        analysis["mrc"] = {}
        sequence = 0

        def item(entry_type: str, value: str, modalities: tuple[str, ...] = ("PDF",)) -> dict:
            nonlocal sequence
            refs = []
            for modality in modalities:
                sequence += 1
                evidence_id = f"I4-GOLD-EVD-{sequence}"
                raw_text = f"BOUND_RAW_EVIDENCE_{entry_type}_{sequence}"
                sections.append({
                    "evidence_id": evidence_id,
                    "entry_type": (
                        "TRC_ESCAPE"
                        if mode == "corrupt" and entry_type == "TRC_OCCURRENCE"
                        else entry_type
                    ),
                    "source_modality": modality,
                    "source_type": "MAJOR_SOURCE_DOCUMENT" if modality == "PDF" else "MAJOR_EXCEL_SOURCE_FACT",
                    "source_id": "ITR-I4-HISTORY",
                    "source_version": "I4-PUB-1",
                    "source_ref": "MAJOR_EVENT:ITR-I4-HISTORY@I4-PUB-1",
                    "origin_source_id": f"SOURCE-{sequence}",
                    "origin_source_version": f"SOURCE-REV-{sequence}",
                    "origin_source_ref": f"SOURCE:SOURCE-{sequence}@SOURCE-REV-{sequence}",
                    "file_name": "history.pdf" if modality == "PDF" else "history.xlsx",
                    "page": 7 if modality == "PDF" else None,
                    "section": entry_type,
                    "raw_text": raw_text,
                    "url": None,
                })
                refs.append({
                    "source_type": modality,
                    "source_location": f"evidence://{evidence_id}",
                    "quote": raw_text,
                })
            source_type = "FUSED" if set(modalities) == {"EXCEL", "PDF"} else modalities[0]
            return {"value": value, "source_type": source_type, "evidence_refs": refs}

        for entry_type, family, side in (
            ("TRC_OCCURRENCE", "trc", "occurrence"),
            ("TRC_ESCAPE", "trc", "escape"),
            ("MRC_OCCURRENCE", "mrc", "occurrence"),
            ("MRC_ESCAPE", "mrc", "escape"),
        ):
            if entry_type == "TRC_ESCAPE":
                analysis[family][side] = {"standard": "", "evidence_refs": []}
                continue
            projected = item(
                entry_type,
                f"Reviewed value: {entry_type}",
                ("EXCEL", "PDF") if entry_type == "TRC_OCCURRENCE" else ("PDF",),
            )
            analysis[family][side] = {
                "standard": projected["value"],
                "evidence_refs": projected["evidence_refs"],
            }

        for entry_type, field in (
            ("TECHNICAL_ACTION", "technical_actions"),
            ("MANAGEMENT_ACTION", "management_actions"),
            ("CORRECTIVE_ACTION", "corrective_actions"),
            ("PREVENTIVE_ACTION", "preventive_actions"),
        ):
            if entry_type == "PREVENTIVE_ACTION":
                case["solution"][field] = []
                continue
            values = [item(entry_type, f"Reviewed value: {entry_type}")]
            if entry_type == "CORRECTIVE_ACTION":
                values.append(item(entry_type, "Second reviewed corrective action"))
            case["solution"][field] = values

    artifacts.save(f"knowledge/enriched_case/{CASE_ID}.json", case)
    artifacts.save(f"knowledge/raw_evidence/{CASE_ID}.json", {
        "case_id": CASE_ID,
        "source_type": "MAJOR_EVENT",
        "source_id": "ITR-I4-HISTORY",
        "sections": sections,
    })
    artifacts.save(f"knowledge/retrieval_docs/{CASE_ID}.json", {
        "case_id": CASE_ID,
        "title": "掉电恢复后启动失败",
        "text": "掉电恢复启动失败",
        "source_case_path": f"knowledge/enriched_case/{CASE_ID}.json",
        "filters": {"knowledge_source": "MAJOR_EVENT"},
    })
    artifacts.save("knowledge/publication_metadata/major_event/i4-golden.json", {
        "publication_status": "PUBLISHED",
        "case_id": CASE_ID,
        "business_id": "ITR-I4-HISTORY",
        "knowledge_revision": "I4-PUB-1",
        "published_at": "2026-10-05T09:00:00+08:00",
    })


def _build_app(root: Path):
    db = root / "p0.sqlite3"
    P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    ).initialize(db)
    p0 = P0Repository(db)
    p0.save_issue(
        knowledge_id=KNOWLEDGE_ID,
        business_issue_id="ITR-I4-CURRENT",
        raw_json={"问题编号": "ITR-I4-CURRENT", "问题描述": "掉电恢复后设备启动失败"},
        normalized_snapshot={"ISSUE_FACT": {
            "business_issue_id": "ITR-I4-CURRENT",
            "title": "当前控制器掉电恢复后启动失败",
            "description": "掉电恢复后设备启动失败",
            "product": "PLC",
            "version": "V4.1",
            "scene": "掉电恢复",
        }},
        mapping_config_id="MAP-PLC-V1",
        mapping_config_version=1,
        standard_catalog_version_id="SFC-PLC-V1",
        source_file_sha256="i4-browser-golden",
        sheet_name="issues",
        row_number=1,
    )
    artifacts = JsonArtifactRepository(root / "artifacts")
    _install_case(artifacts, "typed")

    def repeat_search(_query, top_k):
        MODES["search_calls"] += 1
        if MODES["value"] == "unavailable":
            raise RuntimeError("fixture search unavailable")
        return {"results": [{
            "case_id": CASE_ID,
            "title": "掉电恢复后启动失败",
            "summary": "历史掉电恢复案例",
            "score": 0.91,
            "rank": 1,
            "reasons": ["问题均发生于掉电恢复场景"],
            "matched_fields": ["problem"],
        }][:top_k]}

    case_service = HistoricalCaseConsumerService(artifacts, repeat_search=repeat_search)
    facade = RepeatWebFacade(
        issue_repository=p0,
        repeat_repository=RepeatQueryTraceRepository(root / "repeat-risk.sqlite3"),
        case_service=case_service,
    )
    app = create_p0_app(db, stage_runner=None, repeat_web=facade, major_artifact_root=root / "artifacts")
    return app, artifacts


def main() -> int:
    MODES["search_calls"] = 0
    temp_root = Path(tempfile.mkdtemp(prefix="major-i4-browser-"))
    app, artifacts = _build_app(temp_root)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("I4 browser fixture server did not start")

    base = f"http://127.0.0.1:{port}"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                headless=True,
            )
            page = browser.new_page(viewport={"width": 1440, "height": 1100})
            page.goto(f"{base}/p0/issues/{KNOWLEDGE_ID}", wait_until="networkidle")

            # G1: the existing Repeat Risk page renders the fixed eight typed labels.
            page.click("[data-repeat-query]")
            page.get_by_text("找到 1 个值得关注的历史案例").wait_for()
            expected_labels = [
                "TRC 发生", "TRC 流出", "MRC 发生", "MRC 流出",
                "技术措施", "管理措施", "纠正措施", "预防措施",
            ]
            for label in expected_labels:
                page.locator("[data-repeat-semantic]").filter(has_text=label).wait_for()
            assert page.locator("[data-repeat-semantic]").count() == 8
            page.screenshot(path=str(temp_root / "G1-typed-eight-slots.png"), full_page=True)
            print("G1_BROWSER=PASS")

            # G2: one typed value opens only its own Evidence and lineage.
            page.locator('[data-repeat-semantic-evidence][data-semantic-type="CORRECTIVE_ACTION"]').first.click()
            drawer = page.locator("[data-repeat-evidence-drawer]")
            drawer.wait_for(state="visible")
            drawer_text = drawer.inner_text()
            assert "BOUND_RAW_EVIDENCE_CORRECTIVE_ACTION_" in drawer_text
            assert "BOUND_RAW_EVIDENCE_TRC_OCCURRENCE_" not in drawer_text
            for lineage_label in ("Source Version", "Source Ref", "Origin Source", "Origin Source Ref"):
                assert lineage_label in drawer_text
            page.screenshot(path=str(temp_root / "G2-scoped-evidence.png"), full_page=True)
            page.click("[data-evidence-close]")
            print("G2_BROWSER=PASS")

            # G3: valid missing slots are visible as missing, never synthesized.
            missing = page.locator('[data-repeat-semantic="TRC_ESCAPE"]')
            assert "MISSING" in missing.inner_text()
            assert "未确认 / 无已确认内容" in missing.inner_text()
            assert "GENERIC_FALLBACK_MUST_NOT_DISPLAY" not in page.locator("[data-repeat-risk]").inner_text()
            print("G3_BROWSER=PASS")

            # G4: legacy artifacts retain generic details without typed classification.
            _install_case(artifacts, "legacy")
            page.click("[data-repeat-query]")
            page.locator(".p0-repeat-semantic-legacy").wait_for()
            assert "LEGACY_GENERIC_ONLY" in page.locator("[data-repeat-risk]").inner_text()
            assert page.locator("[data-repeat-semantic]").count() == 0
            assert "LEGACY_ACTION_MUST_NOT_DISPLAY" in page.locator("[data-repeat-risk]").inner_text()
            print("G4_BROWSER=PASS")

            # G5: corrupt evidence is incomplete; no generic fallback or decision.
            _install_case(artifacts, "corrupt")
            page.click("[data-repeat-query]")
            page.get_by_text("查询结果不完整").wait_for()
            restored_state = page.evaluate("async () => (await fetch('/api/v2/issues/K-I4-GOLDEN/repeat-risk')).json()")
            assert restored_state["latest_result"]["candidates"][0]["detail_error"] == "CASE_SEMANTIC_EVIDENCE_INVALID"
            assert restored_state["latest_result"]["human_decision"]["decision"] == "PENDING"
            assert "GENERIC_FALLBACK_MUST_NOT_DISPLAY" not in page.locator("[data-repeat-risk]").inner_text()
            assert page.locator("[data-repeat-state='incomplete'] [data-repeat-decision]").input_value() == ""
            assert page.locator("[data-repeat-result]").count() == 1
            print("G5_BROWSER=PASS")

            # G6: every human choice is explicit and refresh restores that exact saved snapshot.
            choices = ("REPEAT", "SIMILAR", "NOT_REPEAT", "INSUFFICIENT_EVIDENCE")
            for decision in choices:
                _install_case(artifacts, "typed")
                page.click("[data-repeat-query]")
                page.locator("[data-repeat-semantic='TRC_OCCURRENCE']").wait_for()
                page.select_option("[data-repeat-decision]", decision)
                page.fill("[data-repeat-decision-reason]", "G6 manual browser review")
                calls_before_refresh = MODES["search_calls"]
                page.click("[data-repeat-decision-save]")
                page.get_by_text(f"人工结论：{decision}").wait_for()
                page.reload(wait_until="networkidle")
                page.get_by_text(f"人工结论：{decision}").wait_for()
                assert page.locator("[data-repeat-semantic='TRC_OCCURRENCE']").count() == 1
                assert "Reviewed value: TRC_OCCURRENCE" in page.locator("[data-repeat-risk]").inner_text()
                assert MODES["search_calls"] == calls_before_refresh
            print("G6_BROWSER=PASS")
            page.screenshot(path=str(temp_root / "G6-restored-human-decision.png"), full_page=True)
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)

    print(f"BROWSER_EVIDENCE={temp_root}")
    print("I4_G1_G6_BROWSER=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
