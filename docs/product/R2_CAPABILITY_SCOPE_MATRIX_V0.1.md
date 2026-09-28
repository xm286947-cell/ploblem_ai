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
| ITR / 现场恢复 | 当前问题 | SOURCE_FACT_ADAPTER_ACTIVE / SOURCE_WRITE_OWNER_EXTERNAL | NO_REUSABLE_HISTORICAL_HANDLER_FOUND | FORMAL_R2_READ_ONLY_ADAPTER_ACTIVE | authoritative `quality_issue` + retained raw source | `/p0/itr-recovery` + `/itr/recovery-workbench` | `p0_itr_recovery_workbench.html` + `itr_recovery_workbench.html` | `itr_recovery_adapter.py` + Existing Problem service | read source ITR / field-work facts / temporary workaround / source trace / return | QUERY_AND_RETURN_PRESERVED | source-domain write permission retained externally; R2 read-only | YES | Existing Problem | no second ITR master; no Overall state machine | YES | P0 | SOURCE_SYSTEM_WRITE_ACTIONS_NOT_IN_REPO | PR #228 merged; R2 W1 Gate 11/11 PASS; adapter complete, source-system write actions remain external |
| ITR 彻底解决单 | 当前问题 | SOURCE_FACT_PROJECTION_ACTIVE / SOURCE_ACTIONS_EXTERNAL_UNBOUND | SELECTIVELY_ABSORBED_AND_HARDENED | FORMAL_R2_SOURCE_ALIGNED_WORKBENCH_ACTIVE | ITR-CS Source Fact + authoritative existing ITR | `/p0/itr-resolution` + `/itr/resolution-workbench` | `p0_itr_resolution_workbench.html` + `itr_resolution_workbench.html` | `itr_resolution_adapter.py` + `MaterialRepository` | read cause / measures / verification / source business status / source owner / linked issue / return; source save-submit-transition NOT synthesized | QUERY_AND_RETURN_PRESERVED | R2 view is read-only; original source-domain action permission remains external | YES | Existing Problem | no second issue master/state machine; `SOURCE_ACTION_BINDING_PENDING` | YES | P0 | SOURCE_ACTION_CONTRACT_EXTERNAL_PENDING / NON_CODE | PR #229 merged at `e4582ba7`; R2 W1 Gate 12/12 PASS; bind original save/submit/transition only when authoritative source contract is available |
| 软件问题考核 | 当前问题 | HISTORICAL_CAPABILITY_CONFIRMED / IMPLEMENTATION_BINDING_NOT_AVAILABLE | NO_VERIFIED_REUSABLE_IMPLEMENTATION | PROTECTED_EXTERNAL_BINDING_PENDING | Existing Capability only; no substitute | UNVERIFIED — DO NOT INVENT | UNVERIFIED — DO NOT INVENT | UNVERIFIED — DO NOT INVENT | original assessment workflow only | ORIGINAL_STATE_REQUIRED | ORIGINAL_PERMISSION_REQUIRED | ORIGINAL_ROUTE_REQUIRED | Existing business workbench owner / historical integration steward | preserve original business semantics; no second assessment model/state machine | YES when binding exists | P0 | EXTERNAL_FACT_BINDING_PENDING / NON_CODE | repository + active/demo/archive branches + Drive baselines confirm no verifiable Route/Handler/Data Owner; do not use `/analysis` or `software-operations`; exact binding is non-blocking for other R2 development |
| 软件问题漏测分析 | 当前问题 | EXISTING_FACTS_AND_ESCAPE_ANALYSIS_ACTIVE | FORMAL_P0_ADAPTER_COMPLETE | FORMAL_R2_ACTIVE | existing `quality_issue` + Escape Analysis | `/p0/missed-test-analysis` + legacy `/missed-test-analysis` | `p0_missed_test_analysis.html` + `missed_test_analysis.html` | `p0_pages.py` / legacy router + `missed_test_adapter.py` | filter missed-test issues / inspect Escape / open authoritative issue / exact return | QUERY_AND_ANALYSIS_STATUS_PRESERVED | existing problem-domain permission; missing Legacy SoT = 503 fail-closed | YES | Existing Problem | read-only projection; Source Fact `escape.is_escape`; no second missed-test master/state machine | YES | P0 | NONE_FOR_CURRENT_W1_SCOPE / HISTORICAL_ROUTE_UNKNOWN | PR #226 merged at `4491652a`; pre/post-merge R2 W1 Gate 9/9 PASS; legacy route retained, formal P0 page active |
| Legacy Analysis | 当前问题 / Secondary | ACTIVE / REAL_ROUTE | COMPATIBILITY_CANDIDATE | KEEP_COMPATIBLE | current main | `/analysis` | legacy analysis page | `quality_knowledge/web/app.py` | single/batch analysis entry | PRESENT | existing rules | REQUIRED | Existing Problem | legacy compatibility contract | YES | P0 | NONE | preserve exact route and existing behavior; do not repurpose as software assessment |
| Batch Analysis | 当前问题 | ACTIVE / REAL_ROUTE | ENHANCED_CANDIDATE | FORMAL_R2_ACTIVE | current main first | `/p0/batch-analysis` + legacy batch APIs | `p0_batch_analysis.html` | `p0_pages.py` + legacy APIs | batch analyze/retry/status | REQUIRED | existing rules | REQUIRED | Existing Problem | runtime/provider contracts | YES | P0 | NONE | freeze route/deep-link and return semantics |
| Existing Route / Deep Link Compatibility | Shared | VERIFIED_ROUTE_BASELINE_ACTIVE | DEMO_HAS_NAV/ADAPTER/RETURN_CANDIDATES | R2_GATE_ACTIVE | current main + selective absorb | known Legacy/P0 W1 routes including ITR recovery/resolution and missed-test | multi-page | shell/pages/adapters | route/query/back/forward/return | PRESERVED_FOR_VERIFIED_ROUTES | FAIL_CLOSED | REQUIRED | Overall + domain owners | public contracts only | YES | P0 | VERIFIED_SCOPE_PASS / EXTERNAL_ASSESSMENT_BINDING_PENDING | R2 Source `e4582ba7` Gate 12/12 PASS for all currently verifiable W1 routes; software-assessment historical route remains external/unverified and is not fabricated |

## W1 execution status

- W1-SLICE-01 / ITR resolution source-link + round-trip: **PASS_PARTIAL**.
- PR #220 merged to `integration/overall-r2`.
- R2 W1 Gate: compile PASS; focused regression **4 passed**.
- Proven behavior: ITR-CS source import, unique existing-ITR association, no guessed link, discoverable resolution entry, controlled return.
- W1-SLICE-01A / ITR recovery aligned view: PR #228 merged at `c44e5eb8`; Legacy + P0 entries active; raw Source Fact / source trace / query-return / fail-closed behavior verified; R2 Gate **11/11 PASS**. Source-system write actions remain external by design.
- W1-SLICE-01B / ITR resolution source projection: PR #229 merged at `e4582ba7`; cause / measures / verification / source business status / owner are now projected in Legacy + P0 workbenches; no Overall-owned save/submit/transition endpoint exists; R2 Gate **12/12 PASS**.
- Resolution source actions remain `SOURCE_ACTION_BINDING_PENDING`; this is an external Source Contract dependency, not an invitation to invent a workflow.
- Software assessment: repository, current/demo/compat/archive branches and control-plane baselines confirm the capability historically exists, but no verifiable Route / Handler / State Owner / Data Owner is available. Status=`EXTERNAL_FACT_BINDING_PENDING / NON_CODE`; no route invented and no `/analysis` or material-page substitution.
- Missed-test analysis: code/history verification found no reusable independent old page/handler. PR #226 upgraded `/p0/missed-test-analysis` from compatibility redirect to a real unified-host P0 workbench while retaining legacy `/missed-test-analysis`; both use the same Existing Problem SoT. Merge=`4491652abf66eed02959a1ab26010889c59d4cd2`; pre/post-merge W1 Gate **9/9 PASS**. Query + analysis status round-trip is preserved; Legacy returns to `#causes`, P0 returns to `#analysis`; arbitrary/external return targets fail closed.

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
W1_VERIFIED_CODE_SCOPE=PASS
W1_EXTERNAL_BINDINGS_PENDING=2
W1_EXTERNAL_BINDINGS=ITR_RESOLUTION_SOURCE_ACTIONS;SOFTWARE_ASSESSMENT_ORIGINAL_BINDING
NEXT=W1_CLEAN_START_AND_PACKAGE_SMOKE_THEN_W2
