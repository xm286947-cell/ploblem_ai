# QUALITY SCENARIO RC1 — Browser Execution Flow V1.1

Status: FROZEN_FOR_AUDIT_REMEDIATION  
Owner: Product Test Center / Quality Scenario Test Team  
Authority: PRODUCT_TEST_CENTER_V1.1_COMPLIANCE_AUDIT + TEST_AUDIT_QUALITY_SCENARIO_V1.1_001

## Hard rule

For Web Mandatory scenarios, API/TestClient/DB evidence is cross-evidence only. A Golden E2E PASS requires a real browser/user entry from clean state and continuous execution to the terminal business result.

Forbidden Golden shortcuts:
- POST /api/v2/quality-scenarios/candidates/from-reverse from the test runner to create the business mid-state.
- Direct DB insert/update of CANDIDATE / CONFIRMED / PUBLISHED.
- Reusing a previously published scenario created outside the current Golden run.
- Mocking Adapter, Schema, Service, Repository, DB, Review, Confirm, Publish, Query, Detail, Evidence or UI.

## Current product blocker

The existing Reverse Quality page `/reverse-quality/{material_id}` provides Browser AI analysis, field review and missing-information handling.  
P01/P02/P03 provide Browser Review/Confirm/Publish/Query/Detail/Evidence.

However, the current product page has no user action that takes the completed `ReverseQualityResult` into the formal QualityScenario V1 Candidate handoff and opens/selects that Candidate in P01. Engineering tests currently perform this handoff via `/api/v2/quality-scenarios/candidates/from-reverse`.

Therefore BROWSER-GP-01..04 are defined but not executable end-to-end until QS-AUD-P0-002 is fixed. Tests must not bypass the missing user entry.

---

## BROWSER-GP-01 — HIGH_PERCEPTION positive Golden

### Input
Approved synthetic problem with lifecycle/activity/evidence sufficient for publish.

### Browser flow
1. Open source problem/ITR page from clean DB.
2. Navigate to `/reverse-quality/{material_id}`.
3. Select product taxonomy and click **AI 分析本问题**.
4. Wait for analysis page to show lifecycle/activity/quality fields and no blocking missing-information.
5. Verify source facts and Evidence are visible.
6. **Required product handoff:** use the formal user action to create QualityScenario V1 Candidate with:
   - trigger_source=HIGH_PERCEPTION
   - trigger_reason visible and traceable
7. Browser lands on or navigates to `/p0/quality-scenarios/workbench` and selects the created Candidate.
8. Verify P01:
   - Candidate fields
   - trigger source/reason
   - Source Problem
   - Evidence
   - no PENDING blocker
9. Edit one allowed field in browser and click **保存修订**.
10. Refresh and verify edit persists.
11. Fill `[data-quality-actor]` and `[data-technical-actor]`; click **确认**.
12. Verify status becomes CONFIRMED and dual confirmation facts are visible.
13. Click **发布**.
14. Verify status becomes PUBLISHED.
15. Open `/p0/quality-scenarios`; verify default status PUBLISHED.
16. Filter by product, lifecycle, business activity, quality concern and keyword; verify the same scenario remains discoverable.
17. Click the table row `[data-detail="<scenario_id>"]`.
18. On P03 verify:
   - standard scenario definition
   - business goal
   - trigger
   - expected result
   - applicability
   - dual confirmation
   - history
   - Evidence integrity PASS
   - Source Problem/Evidence
19. Expand Source/Evidence and record source_ref/evidence_id.
20. Verify reverse trace resolves to the same scenario.

### PASS
One run_id/scenario_id connects all steps. No API/DB mid-state injection. Provider call count=1 for the AI analysis.

---

## BROWSER-GP-02 — RND_VALUE positive Golden

Same clean-state flow as GP-01, except:
- trigger_source=RND_VALUE
- trigger_reason states the R&D reuse/value reason.
- P01/P02/P03 must display **研发价值** rather than creating any separate approval flow.
- The same CANDIDATE → CONFIRMED → PUBLISHED state machine is used.

PASS only if RND_VALUE itself reaches PUBLISHED/P02/P03/Evidence in one browser run.

---

## BROWSER-GP-03 — Missing Information recovery Golden

### Input
Fixture: `missing_information`.

### Flow
1. Clean source problem → Reverse Quality Browser AI.
2. Verify **待补充信息** section is visible.
3. Attempt to continue without resolving the PENDING item:
   - Confirm/hand-off must not silently advance state.
   - User-visible blocking reason must be shown.
4. Enter a human answer in the visible missing-information control.
5. Set status CONFIRMED (or NOT_APPLICABLE when applicable), provide reviewer, save.
6. Refresh browser.
7. Verify answer/status persist.
8. Complete formal Candidate handoff via user entry.
9. In P01 verify missing information is not PENDING and blocker is gone.
10. Complete Review → dual Confirm → Publish.
11. Verify P02/P03/Evidence/Source trace.

Cross-evidence retained from #127:
- HTTP 400 contract `SCENARIO_MISSING_INFORMATION_PENDING` when PENDING.
- Candidate stays CANDIDATE.
- no raw Pydantic ValidationError leak.

### PASS
The user can both be blocked and recover using product UI; no manual DB/API patch.

---

## BROWSER-GP-04 — Reject terminal Golden

1. Clean source problem → AI → formal Candidate handoff.
2. Open Candidate in P01.
3. Fill professional-quality actor and rejection reason.
4. Click **拒绝** and accept browser confirmation.
5. Refresh.
6. Verify status stays REJECTED.
7. Verify Publish action is unavailable and no PUBLISHED result appears in default P02 library.
8. Record scenario_id and browser evidence.

---

## BROWSER-NEG-01 — Evidence mismatch / insufficient Evidence

1. Trigger AI from Browser with `evidence_mismatch` fixture.
2. Verify the product fails closed or presents an explicit Evidence/missing-information blocker.
3. Verify no publishable Candidate/PUBLISHED scenario exists.
4. Verify no inferred field is presented as a source fact.

---

## BROWSER-NEG-02 — Provider/AI failure

Repeat with:
- rate_limit
- invalid_json
- empty_content

Expected:
- diagnosable user-visible failure
- no fake Candidate/PUBLISHED state
- retry remains available
- no internal exception/secret leakage

---

## BROWSER-CONSUME-01 — P02 query

Must consume a scenario created in the same Golden run. Verify:
- default PUBLISHED
- product
- lifecycle
- business activity
- quality concern
- keyword
- status
- row navigation to P03

## BROWSER-CONSUME-02 — P03 detail / traceability

Must consume the same Golden entity. Verify all frozen detail fields, confirmation facts, history, Evidence integrity, Evidence content pointer and Source Problem trace.

## Mandatory evidence per Browser scenario

- package name + SHA
- test asset commit
- fixture/expected versions
- browser start URL
- screenshot at source problem
- screenshot after AI analysis
- screenshot at Candidate P01
- screenshot after edit/confirm/publish or reject
- P02 result screenshot
- P03 detail/Evidence screenshot
- scenario_id / canonical_itr / run_id where available
- Expected / Actual / Status
- network/API evidence for errors where relevant
- no-secret scan reference

No screenshot/evidence = no PASS.
