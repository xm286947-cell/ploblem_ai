# VNEXT_IMPLEMENTATION_PLAN_V0.1

TASK=OVERALL-VNEXT-MVP-FAST-TRACK-001
STATUS=FAST_MVP_DEMO_READY
SOURCE_MAIN_SHA=11a71c7d0155772044752012ffe3d4cc3546e32a
BRANCH=demo/overall-vnext-fast-mvp
ARCH_REVIEW=PASS_WITH_CONDITIONS
ARCH_BLOCKER_COUNT=0
CODE_CHANGED=YES
PLAN_VERSION=0.1

## Basis

- Approved implementation plan: https://docs.google.com/document/d/1jF843Rc54Krz6WbYESPKdOKP_2JkPmiByBaL7tuHaH4/edit
- Formal takeover task: https://docs.google.com/document/d/1UmcFSgDY_ydM83Fx5JI9a_u5r0TCL9pLGsb9HQH67X0/edit
- Overall product master blueprint: https://docs.google.com/document/d/13VN040aEykuAB_PQb3tlmRQOh9GsOpBY7STK1aIdjf8/edit
- Existing workbench baseline: https://docs.google.com/document/d/1uTTVa1H3RlH0NMKHsPP1CpxS9WCMDO6Jockv47SfPV4/edit
- Existing + new capability mapping: https://docs.google.com/document/d/1D-9x8S6XvAHU2101a__h7LuxF3bXYERzKqp6H48EX_A/edit
- Historical capability integration register: https://docs.google.com/document/d/14c2S4ZUX-370r1UTSiA8N8pDnV9yZ0W1Km_xFPT8xKA/edit

## Implementation classification

### A. Direct reuse

1. Keep `create_p0_app` as the sole FastAPI composition root and retain the existing `api_v2` surface.
2. Reuse the current `/p0/issues` default entry and the existing P0/P1 and product-domain implementations.
3. Reuse the existing platform shell module, template, stylesheet, Evidence navigation and return handling.
4. Preserve each Domain's repository, database, state machine, Source of Truth, and release boundary.

### B. Adapter integration

1. Keep Overall navigation as a presentation/navigation layer over existing workspaces.
2. Use the existing stable shell entries for Major/Repeat, Quality Scenario, Hardware Case, and Storage.
3. Inject task overview data only through the approved provider boundary; no shell access to Domain repositories or tables.
4. Add route compatibility only when a selected capability's concrete route is verified; keep old links usable.

### C. Public Contract integration

1. Use versioned Public Contracts and adapters for cross-domain facts and references.
2. Preserve Common Evidence as reference/projection navigation; do not read another Domain's Evidence store.
3. Preserve Source, object-version, and return-context references through the established contract boundaries.
4. Verify contract compatibility when each consuming milestone reaches implementation.

### D. New implementation

No new shell, web application, port, cross-domain database, or business schema is authorized for M1. New product behavior is limited to the approved workspace backlog and is sequenced in M2-M10 below. Any new component must first pass targeted verification that no existing capability or adapter already provides it.

### E. Blockers

- Architecture blockers: 0.
- M1 blockers: none identified in the approved inputs.
- Feature-local bindings remain pending only for the features that need them: software assessment routes/state owner; legacy `/analysis` relationship to batch analysis; semantic split of `/statistics`; legacy scenario/portrait routes; and per-route `/p0/knowledge/*` Domain ownership. Resolve each with targeted verification immediately before that feature; none reopens the full audit or blocks M1.

### F. Fast MVP demo execution

- The demo branch starts from current `main` at `11a71c7d0155772044752012ffe3d4cc3546e32a`; it is isolated and short-lived. The existing `integration/overall-vnext` candidate branch and PR #212 were left unchanged.
- Current `main` already contains PR #213 and #214 legacy compatibility updates and PR #215's merge of the #207 P04 deterministic state restoration fix.
- Added `scripts/overall_vnext_demo.py` to start the existing `create_p0_app` host with a demo-owned P0 database, Hardware Case database, upload/source directories, and Storage data directory. No production Legacy DB is bound. The script reuses the existing Storage app and does not start another host or port.
- The launcher injects the repository's deterministic P04 fixture provider for `QS-FIX-002` → `PROBLEM-003`; this synthetic data is for the demo only. See `OVERALL_VNEXT_MVP_DEMO_HANDOFF.md` for run steps and fresh evidence. The original M10 candidate record remains in `OVERALL_VNEXT_COMPLETE_PRODUCT_CANDIDATE_M10.md`.
- Run locally with `python scripts/overall_vnext_demo.py --port 8080`; open `http://127.0.0.1:8080/p0/overall`. Demo data is isolated under the operating-system temp directory by default. Use `--data-dir` to choose another isolated directory.
- `python scripts/overall_vnext_demo.py --check` builds the host and requests Overall, Issues, Quality Scenario Insights, and Hardware Case routes without opening a listening socket.
- Overall adds a “案例与知识” area for Major Case, Hardware Case, Published Knowledge, and Unified Knowledge Production, plus direct shortcuts to the other verified legacy-compatible pages. All destinations reuse existing product-owned routes.
- Added product-manager task-space pages for Current Problem, Cases & Knowledge, Quality Scenario & Insights, and Professional Topics, with Management & Configuration as a secondary page. The shared P0 sidebar exposes these routes when the full Overall composition is active.
- Missing unified presentation was implemented in the Overall layer. Existing business pages, APIs, repositories, state machines, and ownership remain in their original products. Legacy-only entries report their runtime binding state instead of failing after navigation.
- The demo launcher isolates the shared Knowledge Production repository alongside P0, Hardware Case, uploads, sources, and Storage data.
- Focused regression after the task-space implementation: Overall shell, parent integration, task-space, Legacy presentation, and case/knowledge entry bindings, URL-bound issue workspace, Major, Quality Scenario, Hardware Case, and Storage binding tests: 26 passed. Existing P0/P1/Knowledge Production page regression: 32 passed. Launcher `--check` passed for the shell, task spaces, shortcuts, P04/P03/source trace, Storage, Published Knowledge, and Knowledge Production routes.
- This demo result records runnable integration status; it is not a TSE gate, release approval, or S11 browser regression.

## Delivery sequence

| Milestone | Scope | Runnable delivery / exit evidence |
| --- | --- | --- |
| M1 | VNext branch + Overall Shell | Canonical branch from approved main; existing unified shell mounted in `create_p0_app`; shell/workspace/route smoke and regression pass. |
| M2 | Current Problem Workspace | Existing ITR/problem capabilities remain reachable; workspace navigation and route compatibility pass without changing the authoritative problem domain. |
| M3 | Cases and Knowledge | Major Case, Repeat Risk, Hardware Case, and published Knowledge entry points integrate by existing contracts; sync current main and regress. |
| M4 | Quality Scenario and Insights | Scenario, portrait, and insight projections integrate with source/evidence trace; old capabilities remain usable until parity. |
| M5 | Specialist Workspaces / Storage | Storage enters through the Overall shell while retaining its internal product structure and domain ownership. |
| M6 | Evidence / Source / Return | Common Evidence, source trace, object version, and return context work end to end across selected journeys; sync current main and regress. |
| M7 | Secondary Management | Management/configuration capabilities are placed by verified business meaning and existing ownership. |
| M8 | Existing Capability Compatibility | Compatibility closure only for capabilities actually consumed by VNext; no renewed whole-repository audit. |
| M9 | End-to-End Integration | Cross-product journeys, route compatibility, public-contract, Evidence/return, and no-second-shell regressions pass; sync current main and regress. |
| M10 | Complete Product Candidate | One runnable candidate with release manifest, regression evidence, rollback path, and handoff to TSE. Canonical PR is the only path to main. |

The M1–M10 records below are historical evidence from the original candidate and its recorded source SHAs. The current fast-demo base, branch, launcher, and fresh focused results are recorded in “Fast MVP demo execution.”

## M1 source verification

The approved main already contains:

- `quality_knowledge/web/overall_shell.py`
- `quality_knowledge/web/templates/overall_shell.html`
- `quality_knowledge/web/static/overall_shell.css`
- `tests/test_overall_frontend_shell.py`
- `create_p0_app` mounts the Overall router when the full platform profile is active.

The shell module is transport/UI-only, exposes four stable workspace entries, uses an injected task provider, and validates same-origin return paths. The minimal M1 dependency closure adds the explicitly imported `httpx` alongside `httpx2` (used by the installed Starlette TestClient path). This is a root dependency declaration only; no product behavior or domain boundary changed. After synchronizing current main at `b0eb3d77bf9cf44162a01fdd79978d266b3caa1f` (Overall URL binding), a clean virtual environment installed the repository root requirements and passed the shell/host regression: `13 passed`.

M1_SYNC_MAIN_SHA=b0eb3d77bf9cf44162a01fdd79978d266b3caa1f
M1_SYNC_MERGE_COMMIT=e6694f3
FIRST_RUNNABLE_TARGET=2026-09-27 (M1 shell/host regression passed)
COMPLETE_CANDIDATE_TARGET=2026-09-27 (candidate assembled; canonical PR is the promotion gate)
M1_RESULT=PASS
TEST_COMMAND=pytest -q tests/test_overall_frontend_shell.py tests/test_overall_frontend_parent_integration.py tests/test_overall_vnext_v03_frontend_binding.py
TEST_RESULT=13 passed

## M2 Current Problem Workspace

- Reused the existing `/p0/issues` page and `/api/v2/issues` contract without changing issue-domain behavior or ownership.
- Kept `/` redirecting to `/p0/issues`; added a direct return link from the existing workspace to `/p0/overall`.
- Verified Overall entry, canonical root route, problem list API, and return navigation in the unified host.

M2_RESULT=PASS
TEST_COMMAND=pytest -q tests/test_overall_frontend_parent_integration.py
TEST_RESULT=5 passed

## M3 Cases and Knowledge

- Synchronized current main at `dffa846cd30f8dcaf92658f40e29c3cc713be5d6`; included its legacy capability compatibility contract without changing its scope.
- Reused the existing Major / Repeat Risk and Hardware Case workspace entries, pages, and Public Contracts.
- Verified that published Historical Case knowledge remains consumable through `historical-case/v1`; no Domain repository was crossed and no duplicate workspace was created.

M3_SYNC_MAIN_SHA=dffa846cd30f8dcaf92658f40e29c3cc713be5d6
M3_RESULT=PASS
TEST_COMMAND=pytest -q tests/test_overall_frontend_parent_integration.py tests/test_major_workspace_binding.py tests/test_hardware_workspace_binding.py tests/test_major_knowledge_publication_contract.py tests/test_historical_case_consumer_contract.py tests/test_hardware_case_knowledge_p0_contract.py
TEST_RESULT=34 passed

## M4 Quality Scenario and Insights

- Reused the existing Quality Scenario Library, P04 insight provider contract, and portrait/archive contracts.
- Verified published scenario identity, source references, Evidence and safe return-context round trip through the existing P0 host.
- No new route, page, or Domain repository integration was needed for this milestone.

M4_RESULT=PASS
TEST_COMMAND=pytest -q tests/test_quality_scenario_workspace_binding.py tests/test_p04_real_integration.py tests/test_p04_portrait_archive.py tests/test_qs_p04_p03_published_binding_fix.py tests/test_quality_insights_drilldown_fix.py
TEST_RESULT=25 passed

## M5 Specialist Workspaces / Storage

- Reused the existing Storage FastAPI product through the approved single-host mount at `/storage-workspace/`.
- Verified Overall navigation, return link, health/API paths, and the standalone Storage host compatibility; Storage retains its own internal structure and database binding.
- No second host or port was added.

M5_RESULT=PASS
TEST_COMMAND=pytest -q tests/test_storage_workspace_binding.py products/storage_rc1/tests/test_ui_next_issue132.py tests/test_overall_frontend_parent_integration.py
TEST_RESULT=14 passed

## M6 Evidence / Source / Return

- Verified current main at `dffa846cd30f8dcaf92658f40e29c3cc713be5d6`; it is already in this branch's merge ancestry from M3, and no newer main merge was found.
- Verified Common Evidence presentation and same-origin return paths in the Overall shell, source contracts for quality scenarios, and Hardware Evidence source metadata / preview lifecycle.
- Kept Evidence as reference and navigation; no producer Domain store is read by the shell.

M6_SYNC_MAIN_SHA=dffa846cd30f8dcaf92658f40e29c3cc713be5d6
M6_RESULT=PASS
TEST_COMMAND=pytest -q tests/test_overall_frontend_shell.py tests/test_overall_frontend_parent_integration.py tests/test_p04_real_integration.py tests/test_hardware_case_evidence_source.py tests/test_source_problem_itr_ref_contract.py
TEST_RESULT=31 passed

## M7 Secondary Management

- Kept product and field mapping, data intake, forward risk assessment, and product reporting on their existing P0 / P1 routes.
- Verified settings, intake, risk assessment, report creation/publication, assets, and existing navigation in their owning workspaces.
- No cross-domain data ownership or route migration was introduced.

M7_RESULT=PASS
TEST_COMMAND=pytest -q tests/test_quality_capability_p0_console_ued.py tests/test_forward_risk_p1a_ued.py tests/test_product_quality_report_mvp.py
TEST_RESULT=15 passed

## M8 Existing Capability Compatibility

- Targeted verification covered only routes consumed by Overall navigation and the current issue, Major / Repeat Risk, Quality Scenario, Hardware Case, and Storage workspaces.
- These existing page and API routes remain reachable with their established return links and contracts.
- The inspected VNext consumers contain no navigation to legacy `/analysis`, `/statistics`, or `/p0/knowledge/*`; issue detail uses the existing versioned `/api/v2/issues/{id}/analysis` endpoint. No whole-repository audit or route migration was opened.
- Other feature-local legacy bindings remain pending until a consuming feature reaches implementation, per the approved targeted-verification rule.

M8_RESULT=PASS
TEST_COMMAND=pytest -q tests/test_overall_vnext_v03_frontend_binding.py tests/test_major_workspace_binding.py tests/test_quality_scenario_workspace_binding.py tests/test_hardware_workspace_binding.py tests/test_storage_workspace_binding.py
TEST_RESULT=12 passed

## M9 End-to-End Integration

- Rechecked current main; `dffa846cd30f8dcaf92658f40e29c3cc713be5d6` is still the latest merged main commit and is already in this branch's ancestry.
- Ran the milestone regression across Overall Shell, issue workspace, Major / Repeat Risk, Hardware Case, Quality Scenario / insights, Storage, Evidence / Source, management, and published knowledge contracts.
- The regression passed without new cross-domain reads or route replacement.

M9_SYNC_MAIN_SHA=dffa846cd30f8dcaf92658f40e29c3cc713be5d6
M9_RESULT=PASS
TEST_COMMAND=pytest -q tests/test_overall_frontend_shell.py tests/test_overall_frontend_parent_integration.py tests/test_overall_vnext_v03_frontend_binding.py tests/test_major_workspace_binding.py tests/test_hardware_workspace_binding.py tests/test_major_knowledge_publication_contract.py tests/test_historical_case_consumer_contract.py tests/test_hardware_case_knowledge_p0_contract.py tests/test_quality_scenario_workspace_binding.py tests/test_p04_real_integration.py tests/test_p04_portrait_archive.py tests/test_qs_p04_p03_published_binding_fix.py tests/test_quality_insights_drilldown_fix.py tests/test_storage_workspace_binding.py products/storage_rc1/tests/test_ui_next_issue132.py tests/test_quality_capability_p0_console_ued.py tests/test_forward_risk_p1a_ued.py tests/test_product_quality_report_mvp.py tests/test_hardware_case_evidence_source.py tests/test_source_problem_itr_ref_contract.py
TEST_RESULT=104 passed

## M10 Complete Product Candidate

- Assembled the release manifest, current-main and branch references, milestone regression evidence, and rollback path in `OVERALL_VNEXT_COMPLETE_PRODUCT_CANDIDATE_M10.md`.
- The original candidate remains recorded on `integration/overall-vnext` / PR #212 and is not changed by this fast-track demo.
- Current demo work is based on the newer `main` SHA above. It does not supersede the canonical branch or represent a TSE/release decision.

M10_RESULT=READY_FOR_TSE
CANDIDATE_DATE=2026-09-27
DEPLOYMENT=NOT_EXECUTED
CANONICAL_PR=https://github.com/xm286947-cell/ploblem_ai/pull/212 (open; base=main)
NEXT=TSE_CANDIDATE_ACCEPTANCE
