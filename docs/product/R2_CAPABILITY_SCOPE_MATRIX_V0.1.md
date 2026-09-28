# R2_CAPABILITY_SCOPE_MATRIX_V0.1

TASK=OVERALL-R2-BRANCH-AND-CAPABILITY-BASELINE-001  
STATUS=FROZEN_V0.1  
R2_BRANCH=integration/overall-r2  
SOURCE_MAIN_SHA=86e5e21e16e33c81587260eb43eb3cf85994e02d  
CURRENT_RELEASE_PRODUCT_BASE=927efef5b7707d5d2013a34c1f3f40a8ade3d93d  
SOURCE_MAIN_DELTA_FROM_PRODUCT_BASE=RELEASE_ENGINEERING_ONLY  
SOURCE_MAIN_DELTA_FILE=.github/workflows/overall-vnext-release-package-927.yml  
DEMO_BRANCH=integration/overall-vnext  
DEMO_BRANCH_HEAD=8a10820acfeb4817d7a17c91f89fc03c28a84673  
DEMO_BRANCH_ROLE=FAST_DEMO_ONLY  
WHOLE_DEMO_MERGE=FORBIDDEN  
PR_212_WHOLE_MERGE=FORBIDDEN  
MAIN_DIRECT_DEVELOPMENT=NO  
CURRENT_RELEASE_BACKWRITE=NO  

## W1 frozen scope

| CAPABILITY | PRODUCT_WORKSPACE | CURRENT_RELEASE_STATUS | DEMO_IMPLEMENTATION_STATUS | TARGET_R2_STATUS | REUSE_SOURCE | ROUTE | PAGE | HANDLER | ACTION | QUERY_STATE | PERMISSION | DEEP_LINK | DOMAIN_OWNER | CONTRACT | TEST_REQUIRED | R2_PRIORITY | BLOCKER | NEXT_ACTION |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 问题工作台 / Current Problem | 当前问题 | ACTIVE / REAL_ROUTE | ENHANCED_CANDIDATE | FORMAL_R2_ACTIVE | current main first | `/p0/issues` + legacy `/issues` | `p0_issues.html` / legacy issue list | `p0_pages.py` + legacy app | list/filter/open issue | PRESENT, preserve query/filter/selection | existing fail-closed rules | REQUIRED | Existing Problem | source-problem-itr-ref/v1 where cross-domain | YES | P0 | NONE | freeze current semantics; add W1 navigation/compatibility coverage |
| ITR / 现场恢复 | 当前问题 | PARTIAL / ISSUE_DETAIL_PRESENT | CANDIDATE / NEED_DIFF_REVIEW | FORMAL_WORKBENCH_DISCOVERABLE | current main + selective demo absorb only if proven | `/p0/issues/{knowledge_id}` | `p0_issue_detail.html` | `p0_pages.py` / issue-detail JS | inspect ITR, current context, recovery flow | MUST_PRESERVE | existing domain permission | REQUIRED | Existing Problem | source-problem-itr-ref/v1 | YES | P0 | PRODUCT_ENTRY_PARITY_NOT_FROZEN | targeted verification of real ITR/recovery entry and actions |
| ITR 彻底解决单 | 当前问题 | SOURCE_LINKED_INDEX_ACTIVE / FULL_FLOW_NOT_YET_BOUND | SELECTIVELY_ABSORBED | FORMAL_R2_ACTIVE | R2 selective absorb from demo candidate | `/itr/resolution-workbench` + linked legacy issue detail | `itr_resolution_workbench.html` + existing issue detail | legacy router + `MaterialRepository` | source list / unique ITR link / return; save-submit-flow still pending historical binding | RETURN_PRESERVED | existing problem-domain permission; explicit R2 permission parity still required | YES | Existing Problem | no second issue master; source materials link to authoritative `quality_issue` | YES | P0 | FULL_SAVE_SUBMIT_FLOW_BINDING_PENDING | PR #220 merged; R2 Gate 4/4 PASS; continue targeted verification for original state/actions |
| 软件问题考核 | 当前问题 | CAPABILITY_KNOWN / FORMAL_ENTRY_NOT_FROZEN | TARGETED_VERIFICATION_REQUIRED | FORMAL_R2_ACTIVE | Existing Capability first | UNKNOWN_AT_W0 | UNKNOWN_AT_W0 | UNKNOWN_AT_W0 | assessment workflow | REQUIRED | REQUIRED | REQUIRED | Existing Problem | preserve existing business semantics | YES | P0 | ROUTE_PAGE_HANDLER_UNKNOWN | targeted verification only; do not map to `/analysis` as substitute |
| 软件问题漏测分析 | 当前问题 | EXISTING_FACTS_AND_ESCAPE_ANALYSIS_ACTIVE | NO_REUSABLE_INDEPENDENT_UI_FOUND | FORMAL_R2_ADAPTER_ACTIVE | existing `quality_issue` + Escape Analysis | `/p0/missed-test-analysis` → `/missed-test-analysis` | `missed_test_analysis.html` | Existing Problem adapter `missed_test_adapter.py` | filter missed-test issues / inspect Escape / open authoritative issue / return | QUERY_PRESERVED | existing problem-domain permission; no new state owner | YES | Existing Problem | read-only projection; no second missed-test master | YES | P0 | NONE_FOR_ADAPTER / HISTORICAL_ROUTE_UNKNOWN | PR #221 merged; R2 Gate 7/7 PASS; keep historical route unknown rather than fabricate it |
| Legacy Analysis | 当前问题 / Secondary | ACTIVE / REAL_ROUTE | COMPATIBILITY_CANDIDATE | KEEP_COMPATIBLE | current main | `/analysis` | legacy analysis page | `quality_knowledge/web/app.py` | single/batch analysis entry | PRESENT | existing rules | REQUIRED | Existing Problem | legacy compatibility contract | YES | P0 | NONE | preserve exact route and existing behavior; do not repurpose as software assessment |
| Batch Analysis | 当前问题 | ACTIVE / REAL_ROUTE | ENHANCED_CANDIDATE | FORMAL_R2_ACTIVE | current main first | `/p0/batch-analysis` + legacy batch APIs | `p0_batch_analysis.html` | `p0_pages.py` + legacy APIs | batch analyze/retry/status | REQUIRED | existing rules | REQUIRED | Existing Problem | runtime/provider contracts | YES | P0 | NONE | freeze route/deep-link and return semantics |
| Existing Route / Deep Link Compatibility | Shared | KNOWN_ROUTE_BASELINE_ACTIVE | DEMO_HAS_NAV/ADAPTER/RETURN_CANDIDATES | R2_GATE_REQUIRED | current main + selective absorb | known Legacy/P0 W1 routes | multi-page | shell/pages/adapters | route/query/back/forward/return | PRESERVED_FOR_TESTED_ROUTES | FAIL_CLOSED | REQUIRED | Overall + domain owners | public contracts only | YES | P0 | PARTIAL_GATE_PASS / UNKNOWN_ASSESSMENT_BINDING | PR #225 merged; R2 W1 Gate 9/9 PASS for known routes; continue only unknown ITR-recovery / software-assessment bindings |

## W1 execution status

- W1-SLICE-01 / ITR resolution source-link + round-trip: **PASS_PARTIAL**.
- PR #220 merged to `integration/overall-r2`.
- R2 W1 Gate: compile PASS; focused regression **4 passed**.
- Proven behavior: ITR-CS source import, unique existing-ITR association, no guessed link, discoverable resolution entry, controlled return.
- Not yet claimed complete: original resolution save / submit / workflow-state binding.
- Software assessment: historical capability confirmed; exact Route / State Owner remains `NEEDS_OWNER_CONFIRMATION`; no route invented.
- Missed-test analysis: code/history verification found no reusable independent page/handler in current, demo, legacy-compatibility or archived branches. R2 read-only adapter is now active at `/p0/missed-test-analysis` → `/missed-test-analysis`; PR #221 merged; final W1 Gate **7 passed**. Historical old route remains unknown and is not claimed.

## Selective absorb candidates

The following are **candidate groups**, not approved code merges:

1. ITR resolution workbench candidate — demo file includes `quality_knowledge/web/templates/itr_resolution_workbench.html`.
2. Overall Navigation candidate — demo changes include `overall_navigation.py/css/js`.
3. Existing Page Adapter / Legacy compatibility candidate — demo changes include web app/shell/pages and compatibility tests.
4. Windows launcher candidate — demo includes `START_OVERALL_VNEXT_WINDOWS.bat` and Windows package/start scripts.
5. Evidence / Source / Return candidate — demo includes Overall Evidence Drawer / return-state work and related tests.

SELECTIVE_ABSORB_CANDIDATE_COUNT=5

Each candidate must go through:

`Targeted Diff Review → Product Semantic Review → Test Evidence Review → Selective Absorb / Cherry-pick / Re-implement`

No whole-branch merge is authorized.

## W1 DoD

W1_CAPABILITY_COUNT=8

W1 is complete only when:

- ITR / 现场恢复 is a real discoverable product entry, not `/materials/itr`.
- ITR 彻底解决 is a real workbench with preserved business actions/state.
- 软件问题考核 has its own real entry and is not aliased to `/analysis`.
- 软件问题漏测分析 has its own real entry and is not represented only by an escape field.
- `/analysis` and `/p0/batch-analysis` remain compatible.
- Critical W1 routes preserve query, filter, selected item, permission, deep link, browser back/forward and return context.
- No second issue master data, Web shell, FastAPI host, or port is introduced.

## Boundary decisions

CURRENT_RELEASE_BASELINE=927efef5b7707d5d2013a34c1f3f40a8ade3d93d  
R2_SOURCE_MAIN=86e5e21e16e33c81587260eb43eb3cf85994e02d  
R2_SOURCE_MAIN_PRODUCT_DELTA=NONE  
R2_SOURCE_MAIN_RELEASE_TOOLING_DELTA=YES  
DEMO_AUTO_PROMOTION=NO  
OPEN_PRODUCT_BLOCKER=0  
NEXT=W1_CURRENT_PROBLEM_EXISTING_WORKBENCH_COMPLETE
