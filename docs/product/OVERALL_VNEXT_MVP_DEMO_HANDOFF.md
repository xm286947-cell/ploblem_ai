# Overall VNext MVP Demo Handoff

TASK=OVERALL-VNEXT-MVP-FAST-TRACK-001
STATUS=RUNNABLE_LOCAL_DEMO
SOURCE_MAIN_SHA=11a71c7d0155772044752012ffe3d4cc3546e32a
DEMO_BRANCH=demo/overall-vnext-fast-mvp
PR207_CHANGESET_IN_MAIN=YES
PR215_MERGE_COMMIT=11a71c7d0155772044752012ffe3d4cc3546e32a
BASELINE_CANDIDATE_PR=212 (left unchanged)
DEPLOYMENT=LOCAL_DEMO_ONLY

## What the demo includes

- Existing unified `create_p0_app` host and Overall Shell at `/p0/overall`.
- Existing issue workspace, Major / Repeat Risk, Quality Scenario, Hardware Case, and Storage entry points.
- Overall Shell shortcuts to existing Current Problem, batch analysis, quality insights, P1 reports/risk, data intake, and mapping pages.
- A four-entry “案例与知识” area linked to Major Case, Hardware Case, Published Knowledge, and Unified Knowledge Production pages.
- Product-manager task spaces for Current Problem, Cases & Knowledge, Quality Scenario & Insights, and Professional Topics, plus a Management & Configuration secondary page.
- Overall task-space navigation in the shared P0 sidebar. Repeat Risk remains embedded in the current-problem journey.
- The current `main` changes from PRs #213/#214 and #215, including the complete #207 state-restoration fix.
- Deterministic synthetic P04 data from the repository's `FixtureP04Provider`, including `QS-FIX-002` and source `PROBLEM-003`.
- Isolated P0, Hardware Case, uploads, sources, Storage, and shared Knowledge Production data paths. The launcher clears any inherited Legacy DB binding so the demo does not touch a production Legacy DB or the repository's default Knowledge Production data.

The fixture is visibly identified in startup output as `P04_DEMO_DATA=SYNTHETIC_QS-FIX_FIXTURES`; it is demo data and does not represent customer records.

## Run

```bash
python scripts/overall_vnext_demo.py --port 8080
```

Open `http://127.0.0.1:8080/p0/overall`, then select Quality Scenario. The P04 fixture presents the Industry view and scenario `QS-FIX-002`.

The default data directory is the OS temp directory at `overall-vnext-fast-mvp`. Use `--data-dir /path/to/isolated/demo-data` to choose a separate location.

To initialize and smoke key routes without opening a port:

```bash
python scripts/overall_vnext_demo.py --check
```

## Verification

- Focused Overall/workspace regression: **26 passed** (Overall Shell, product task spaces, Legacy binding presentation, parent integration, case/knowledge navigation, shortcut route bindings, URL-bound issue workspace, Major, Quality Scenario, Hardware Case, and Storage binding).
- Existing P0/P1/Knowledge Production page regression: **32 passed**.
- Launcher `--check`: **PASS** for Overall, Issues, P04, Hardware Case, Storage, Published Knowledge, Unified Knowledge Production, default redirect, the synthetic P04 query, P03 detail, and Source Trace page.
- Formal S11 browser regression was not run. The smoke confirms demo route availability only; it does not claim S11 acceptance.
- Full historical M1–M9 result of 104 tests is recorded in the original implementation plan and was not rerun on this demo branch.

This is a runnable local MVP demo, not a production deployment, TSE gate, or release approval.
