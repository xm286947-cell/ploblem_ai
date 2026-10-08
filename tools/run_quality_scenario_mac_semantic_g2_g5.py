#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path

from quality_knowledge.web.app import create_app
from tools.build_quality_scenario_test_fixture import advance_g5, build_fixture
from tools import run_quality_scenario_mac_semantic_e2e as sem


PRODUCT_DUT_SHA = os.environ.get(
    "QS_PRODUCT_DUT_SHA",
    "5d9538f189fb3ca6f056ea57ad0d666f6e6b9002",
)


def _markdown(report: dict) -> str:
    lines = [
        "# Quality Scenario macOS Real-Provider Semantic E2E — G2-G5 Continuation",
        "",
        f"- Product DUT SHA: {report['product_dut_sha']}",
        f"- Provider: {report['provider']}",
        f"- Overall hard gate: {report['overall_hard_gate']}",
        "",
        "G1 is intentionally not rerun. This continuation executes G2, G3, G4 and G5 only.",
        "",
    ]
    for case_id in ("G2_NO_MISSED_TEST", "G3_CONFLICT", "G4_DUPLICATE_GENERATE", "G5_SOURCE_REVISION"):
        item = report["cases"][case_id]
        lines += [f"## {case_id}", ""]
        if case_id == "G3_CONFLICT":
            lines += [
                f"- Preview state: {item.get('preview_state')}",
                f"- Generate disabled: {item.get('generate_disabled')}",
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
            f"- Name: {scenario.get('scenario_name')}",
            f"- Description: {scenario.get('scenario_description')}",
            f"- Business goal: {scenario.get('business_goal')}",
            f"- Trigger: {scenario.get('trigger_condition')}",
            f"- Expected result: {scenario.get('expected_result')}",
            f"- Scope: {scenario.get('applicability_scope')}",
            f"- Forbidden hits: {', '.join(evaluation['forbidden_hits']) or 'none'}",
            "",
        ]
        if case_id == "G5_SOURCE_REVISION":
            lines += [
                f"- Old scenario ID: {item.get('old_scenario_id')}",
                f"- New scenario ID: {item.get('new_scenario_id')}",
                f"- Lineage changed: {item.get('lineage_changed')}",
                "",
            ]
    return "\n".join(lines) + "\n"


def main() -> int:
    model_config = Path(
        os.environ.get("REVERSE_QUALITY_MODEL_CONFIG")
        or os.environ.get("QS_SEMANTIC_MODEL_CONFIG")
        or (sem.ROOT / "config/runtime/model.yaml")
    ).expanduser().resolve()
    if not model_config.exists():
        raise SystemExit(f"SEMANTIC_MODEL_CONFIG_NOT_FOUND:{model_config}")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise SystemExit("PLAYWRIGHT_REQUIRED") from error

    evidence_dir = Path(
        os.environ.get(
            "QS_SEMANTIC_EVIDENCE_DIR",
            sem.ROOT / "validation" / "mac_semantic_g2_g5",
        )
    ).resolve()
    evidence_dir.mkdir(parents=True, exist_ok=True)

    source_db = evidence_dir / "quality_scenario_semantic_source.db"
    qsv1_db = evidence_dir / "quality_scenario_semantic_result.db"
    for path in (source_db, qsv1_db):
        if path.exists():
            path.unlink()

    manifest = build_fixture(source_db)
    os.environ["QUALITY_SCENARIO_V1_DB_PATH"] = str(qsv1_db)
    os.environ["REVERSE_QUALITY_MODEL_CONFIG"] = str(model_config)

    report = {
        "contract": "quality-scenario-mac-real-provider-semantic-g2-g5/v1",
        "product_dut_sha": PRODUCT_DUT_SHA,
        "provider": "real/openai_compatible",
        "model_config_name": model_config.name,
        "source_fixture_contract": manifest.get("contract"),
        "g1_rerun": False,
        "direct_qsv1_candidate_write": False,
        "direct_qsv1_publish_write": False,
        "cases": {},
    }

    app_port = sem._free_port()
    with sem._running_app(create_app(source_db), app_port):
        base = f"http://127.0.0.1:{app_port}"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1600, "height": 1200})

            for case_id in ("G2_NO_MISSED_TEST", "G4_DUPLICATE_GENERATE"):
                case = sem._case(manifest, case_id)
                scenario_id = sem._generate(
                    page,
                    base,
                    case["software_assessment_material_id"],
                    f"MAC_REAL_SEMANTIC_{case_id}",
                )
                scenario = sem._get_json(
                    page,
                    base + "/api/v2/quality-scenario-workflow/v1/quality-scenarios/" + scenario_id,
                )
                trace = sem._get_json(
                    page,
                    base + "/api/v2/quality-scenario-workflow/v1/quality-scenarios/" + scenario_id + "/traceability",
                )
                page.goto(base + "/quality-scenarios/library/" + scenario_id, wait_until="networkidle")
                page.screenshot(path=str(evidence_dir / f"{case_id}.png"), full_page=True)
                report["cases"][case_id] = {
                    "scenario": scenario,
                    "trace": trace,
                    "evaluation": sem._evaluate(case_id, scenario, trace),
                }

            g3 = sem._case(manifest, "G3_CONFLICT")
            sem._prepare_case(
                page,
                base,
                g3["software_assessment_material_id"],
                "MAC_REAL_SEMANTIC_G3",
            )
            page.locator('[data-state="INFORMATION_REQUIRED"]').wait_for(timeout=30000)
            g3_disabled = page.locator("[data-generate]").is_disabled()
            page.screenshot(path=str(evidence_dir / "G3_CONFLICT.png"), full_page=True)
            report["cases"]["G3_CONFLICT"] = {
                "preview_state": "INFORMATION_REQUIRED",
                "generate_disabled": g3_disabled,
            }

            g5 = sem._case(manifest, "G5_SOURCE_REVISION")
            old_id = sem._generate(
                page,
                base,
                g5["software_assessment_material_id"],
                "MAC_REAL_SEMANTIC_G5",
            )
            old_scenario = sem._get_json(
                page,
                base + "/api/v2/quality-scenario-workflow/v1/quality-scenarios/" + old_id,
            )
            old_trace = sem._get_json(
                page,
                base + "/api/v2/quality-scenario-workflow/v1/quality-scenarios/" + old_id + "/traceability",
            )
            sem._publish(page, base, old_id, "G5_SOURCE_REVISION")
            advance_g5(source_db)

            sem._prepare_case(
                page,
                base,
                g5["software_assessment_material_id"],
                "MAC_REAL_SEMANTIC_G5",
            )
            page.locator('[data-state="SOURCE_CHANGED_REANALYSIS_AVAILABLE"]').wait_for(timeout=30000)
            page.locator("[data-generate]").click()
            page.locator('[data-state="CANDIDATE_CREATED"]').wait_for(timeout=420000)
            new_id = sem._scenario_id_from_actions(page)
            new_scenario = sem._get_json(
                page,
                base + "/api/v2/quality-scenario-workflow/v1/quality-scenarios/" + new_id,
            )
            new_trace = sem._get_json(
                page,
                base + "/api/v2/quality-scenario-workflow/v1/quality-scenarios/" + new_id + "/traceability",
            )
            page.goto(base + "/quality-scenarios/library/" + new_id, wait_until="networkidle")
            page.screenshot(path=str(evidence_dir / "G5_SOURCE_REVISION_NEW.png"), full_page=True)
            report["cases"]["G5_SOURCE_REVISION"] = {
                "old_scenario": old_scenario,
                "old_trace": old_trace,
                "scenario": new_scenario,
                "trace": new_trace,
                "old_scenario_id": old_id,
                "new_scenario_id": new_id,
                "lineage_changed": old_id != new_id,
                "evaluation": sem._evaluate("G5_SOURCE_REVISION", new_scenario, new_trace),
            }

            browser.close()

    hard_failures = []
    for case_id in ("G2_NO_MISSED_TEST", "G4_DUPLICATE_GENERATE", "G5_SOURCE_REVISION"):
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

    (evidence_dir / "semantic_g2_g5_result.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (evidence_dir / "SEMANTIC_G2_G5_REPORT.md").write_text(
        _markdown(report),
        encoding="utf-8",
    )

    print("TASK=QUALITY_SCENARIO_MAC_REAL_SEMANTIC_E2E_CONTINUE_G2_G5_008")
    print(f"PRODUCT_DUT_SHA={PRODUCT_DUT_SHA}")
    print("G1_RERUN=NO")
    print(f"OVERALL_HARD_GATE={report['overall_hard_gate']}")
    print(f"G2_SEMANTIC_SMOKE={report['cases']['G2_NO_MISSED_TEST']['evaluation']['semantic_smoke']}")
    print(f"G3_FAIL_CLOSED={'PASS' if report['cases']['G3_CONFLICT']['generate_disabled'] else 'FAIL'}")
    print(f"G4_SEMANTIC_SMOKE={report['cases']['G4_DUPLICATE_GENERATE']['evaluation']['semantic_smoke']}")
    print(f"G5_SEMANTIC_SMOKE={report['cases']['G5_SOURCE_REVISION']['evaluation']['semantic_smoke']}")
    print(f"G5_LINEAGE={'PASS' if report['cases']['G5_SOURCE_REVISION']['lineage_changed'] else 'FAIL'}")
    print(f"EVIDENCE_DIR={evidence_dir}")
    return 0 if not hard_failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
