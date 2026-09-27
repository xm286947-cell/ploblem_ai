# VNEXT_IMPLEMENTATION_PLAN_V0.1

TASK=OVERALL-VNEXT-SINGLE-DEV-TAKEOVER-001
STATUS=ACTIVE
SOURCE_MAIN_SHA=f232c6d199ff8a943cd3821e2e111c7f8ff31d91
BRANCH=integration/overall-vnext
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
COMPLETE_CANDIDATE_TARGET=After M9 integration and M10 release assembly; schedule estimate pending M2 sizing.
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
NEXT=M6_EVIDENCE_SOURCE_AND_RETURN
