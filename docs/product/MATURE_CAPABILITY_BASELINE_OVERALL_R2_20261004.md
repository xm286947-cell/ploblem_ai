# Mature Capability Baseline — Overall R2 / QSV1

Audit frozen before implementation changes.

```text
MATURE_BASELINE_FROZEN=YES
BASE_BRANCH=integration/overall-r2
BASE_SHA=9ac6ae0602d7ac2812e3f063b2231424094e2ee9
DEV_BRANCH=feature/step1b-qsv1-mature-preview-316
DEV_SHA=9153751d2d68a6bd877b7f7785dea6aeb47430ad
PR_317=OPEN
ISSUE_316=OPEN
```

## Route and implementation map

| Capability / route | Page and data source | Existing action and return context | Existing implementation | Current R2 state | Restore action |
| --- | --- | --- | --- | --- | --- |
| Issues `/issues`, `/issues/{knowledge_id}` | Mature `issues.html` / `issue_detail.html`; legacy Quality Issue DB repositories/services | Search, filters, paging, detail, human/quality confirmations, issue period; detail preserves a validated `return_to` | `quality_knowledge/web/app.py:565-695`, `create_legacy_quality_issue_router` at `:211` | Direct mature routes are mounted by `create_app`; Overall current-problem area also points at mature ITR/software/missed-test routes (`overall_shell.py:71-84`) | Preserve direct route and page; expose it in navigation from the QSV1 shell |
| ITR recovery `/itr/recovery-workbench` | `itr_recovery_workbench.html`; `material_repo` plus linked Quality Issue service | Search, source-fact display, issue detail link and return path | `app.py:341-359`; `itr_recovery_adapter.py` | Mature route exists; `/p0/itr-recovery` is a separate condensed page in `p0_pages.py:342-371` | QSV1 navigation must use the mature route |
| ITR resolution `/itr/resolution-workbench` | `itr_resolution_workbench.html`; `material_repo` and linked issue identity | Search, material state, linked issue detail and return path | `app.py:321-339`; `itr_resolution_adapter.py` | Mature route exists; `/p0/itr-resolution` is a separate condensed page in `p0_pages.py:374-406` | QSV1 navigation must use the mature route |
| Software assessment `/software-assessment` | `software_assessment_workbench.html`; software-operation source materials and assessment facts | Search, issue association, department/person, assessment status/result and detail return | `app.py:380-401`; `software_assessment_adapter.py` | Mature route exists; `/p0/software-assessment` is a separate condensed page in `p0_pages.py:408-441` | QSV1 navigation must use the mature route |
| Missed-test analysis `/missed-test-analysis` | `missed_test_analysis.html`; mature Quality Issue analysis service | Search/status filters, analysis facts, issue detail return to `#causes` | `app.py:361-378`; `missed_test_adapter.py` | Mature route exists; `/p0/missed-test-analysis` is a separate condensed page in `p0_pages.py:310-340` | QSV1 navigation must use the mature route |
| Product reports `/product-reports` | Mature report page and `ProductReportService` projections | Precheck, create, view, publish and delete governed reports | `app.py:915-946`; mature P1 page in `p1_pages.py:25-28` | Mature report routes and APIs exist; `/p1/product-reports` is a separate shell alias | Keep `/product-reports` as the mature navigation target |
| Import `/import`, `/import/preview`, `/import/confirm`, `/imports/{batch_id}` | Mature import pages; mapping, intake-session and issue-import services | Preview, validation, confirm, batch result; mapping revision is rechecked before commit | `app.py:278-280`, `:538-563` | Mature import lifecycle exists | Keep `/import` as the mature entry |
| Legacy scenarios `/quality-scenarios` | Mature editable scenario library backed by legacy `quality_scenario*` tables and scenario services | Search/filter, detail/edit, candidate generation, standardization, capability gaps, taxonomy/semantics, delete/retry; detail links back to library | Reusable restoration commit `7cd0c09`: `quality_knowledge/web/app.py:477-706`, `quality_knowledge/scenarios.py`, `quality_knowledge/scenario_generation.py`, `quality_knowledge/scenario_semantics.py` | The mature implementation is absent from both base and QSV1 head. Base only has a read-only compatibility adapter in `legacy_scenario_compat.py`, and `knowledge-web` does not mount that adapter | Directly reuse commit `7cd0c09` in Phase 1; keep all existing actions and data semantics |
| Scenario assets and business insights `/quality-scenario-assets`, `/quality-scenarios/insights` | Mature asset overview/detail and scenario insight pages over existing scenario, evidence, scope, and asset tables | Search/group/context/metric actions and product/industry drill-down; asset pages link back to scenario detail | Reusable restoration commit `7cd0c09`: `scenario_asset_pages.py:65-176`, `app.py:563+`, `scenario_assets.py`, `quality_scenario_insights.html` | Full mature implementation is absent from base and QSV1 head; R2 read-only compatibility pages are not a substitute | Directly reuse commit `7cd0c09`; retain original routes/actions |
| Customer / industry portraits `/quality-scenario-assets/portrait` | Mature portrait page and archive/interpretation services over customer/industry/scope/evidence data | Customer/industry/product pivots, filters, archive/interpretation navigation, return to asset view | Reusable restoration commit `7cd0c09`: `scenario_asset_pages.py:100-157`, `scenario_customer_portrait.html`, portrait/interpretation services | Full mature implementation is absent from base and QSV1 head | Directly reuse commit `7cd0c09`; retain original portrait context and source data |
| Scenario taxonomy/config `/settings/scenario-taxonomy` and semantic settings | Mature taxonomy/config pages backed by existing scenario repository and semantic services | Draft/version/activate taxonomy; edit lifecycle/activity; import; review semantic terms | Reusable restoration commit `7cd0c09`: `app.py:645-706`, `scenario_taxonomy.html`, `scenario_semantics.html` | Full scenario management configuration is absent from base and QSV1 head; generic P0 settings are not equivalent | Directly reuse commit `7cd0c09` and keep the mature configuration actions |
| QualityScenario V1 `/p0/quality-scenarios/workbench`, `/p0/quality-scenarios`, `/p0/quality-scenarios/library/{scenario_id}`, `/p0/quality-scenario-insights` | Existing V1 workbench/library/detail/P04 pages; `quality_scenario_v1*` tables and published-only `QualityScenarioV1P04Provider` | Candidate → Review → Confirm → Publish; library/detail/source/evidence/history; context-aware return | Added by PR #317; `quality_scenario_v1_api.py`, V1 workflow/store/services, `p0_pages.py`, V1 templates | Additive and separate from legacy `quality_scenario*`; old mature `base.html` adds the preview link | Reuse existing V1 modules; repair only host navigation/binding needed for mature coexistence |

## Audit findings

1. Mature issue, ITR, software assessment, missed-test, import, and report pages and service paths are present in the base tree and are not
   replaced in the mature `create_app` router. `main.py knowledge-web` calls
   `create_app` (`main.py:428-431`); the mature handlers are in `app.py`.
2. Overall R2 capability metadata already points its four current-problem entries
   at mature routes (`overall_shell.py:71-84`) and its old scenario entries at the
   legacy read-only routes (`:99-113`).
3. The complete mature legacy Quality Scenario implementation is not present in
   the R2 base/QSV1 head. Git history has the accepted restoration commit
   `7cd0c09` (`restore mature legacy Quality Scenario and Portrait capabilities
   (#299)`), which adds the original services, pages, actions, and management
   routes. This commit descends from the frozen R2 base and is the direct reuse
   source for Phase 1.
4. The QSV1 workbench, library, detail, and insight templates extend
   `p0_base.html`. When embedded in the mature host, that shell's default sidebar
   points its ITR, resolution, software assessment, and missed-test entries to
   the condensed `/p0/...` pages (`templates/p0_base.html:27-41`). Those pages
   duplicate mature facts in a smaller presentation (`p0_pages.py:310-441`).
   This is the concrete navigation regression to repair.
5. The mature `base.html` already has the QSV1 preview entry and retains mature
   links for ITR, resolution, software assessment, missed-test, reports, import,
   and configuration (`templates/base.html:10-32`).
6. The local database at `ploblem_ai/knowledge/quality_issue_v1.db` and the
   `Documents/Codex/2026-09-29/mac-step1-startup-check/ditto-r1` copy have the
   expected mature schema but zero `quality_issue`, `quality_issue_version`,
   `source_material`, and legacy `quality_scenario` rows. They cannot establish
   a real-data product regression. No fixture or seed data will be used as a
   substitute.
7. The QSV1 provider's data contract is explicit: only PUBLISHED V1 rows feed
   P04; empty real data yields zero/EMPTY and `SYNTHETIC_FIXTURE_USED=NO`.

## Current status before implementation

```text
A_MATURE_ISSUE_ITR_ASSESSMENT_MISSED_IMPORT_REPORT_IMPLEMENTATIONS_PRESENT=YES
B_MATURE_LEGACY_QS_IMPLEMENTATION_PRESENT_IN_BASE=NO
C_P0_DUPLICATE_NAV_IN_QSV1_SHELL=YES
D_MATURE_ISSUE_ROUTER_MOUNTED_BY_KNOWLEDGE_WEB=YES
E_REAL_MATURE_DATA_AVAILABLE_LOCALLY=NO
RESTORE_ACTION=Directly reuse commit 7cd0c09 for the complete mature legacy QS
services/pages/actions; wire it into the existing knowledge-web host; repair
QSV1 shell navigation to preserve mature routes and retain old QS side-by-side.
PHASE_1_PRODUCT_GATE=BLOCKED_PENDING_REAL_MATURE_DATABASE
```

## Phase 1 implementation checkpoint (2026-10-04)

The frozen direct-reuse restoration has been applied on the QSV1 development
branch using the existing #299 history:

- `876cd29` restores the mature legacy Quality Scenario library, asset pages,
  portrait, insights, taxonomy, services, and original actions.
- `30c17cd` restores the mature scenario-domain helper compatibility.
- `99e1006` restores the RC1 scenario-evidence enrichment module.
- `052df90` restores the RC1 reporting-year compatibility table required by
  the mature portrait/material readers.
- `quality_knowledge/web/templates/base.html` exposes the restored legacy
  scenario library, asset/portrait, insights, and taxonomy routes alongside
  the existing mature navigation and QSV1 preview entry.

Focused compatibility evidence: `tests/test_r2_w2_legacy_scenario_compat.py`
and `tests/test_r2_w2_projection_parity.py` pass (6 tests). This evidence is
structural/contract-level only; its test data is not represented as real
business data. A route smoke with an empty temporary database also exposed and
then verified the required reporting-year compatibility schema, but it is not
a product regression.

The available local databases remain empty of `quality_issue`,
`quality_issue_version`, `source_material`, and legacy `quality_scenario`
records. Therefore the Phase 1 real-business-data checks (search/filter/detail,
associations, mature workflow actions, historical reports, and scenario
content) cannot be performed. Strict order requires stopping here:

```text
MATURE_PLATFORM_RESTORE=BLOCKED_PENDING_REAL_MATURE_DATABASE
EXISTING_CAPABILITY_REGRESSION=NOT_STARTED
QSV1_ADDITIVE_INTEGRATION=NOT_STARTED
WINDOWS_NATIVE_GATE=NOT_STARTED
MACOS_NATIVE_GATE=NOT_STARTED
NEW_PREVIEW_PACKAGE=NOT_BUILT
SYNTHETIC_FIXTURE_USED=NO
```
