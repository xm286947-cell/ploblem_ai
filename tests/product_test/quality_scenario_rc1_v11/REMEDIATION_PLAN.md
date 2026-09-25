# QUALITY SCENARIO RC1 — V1.1 Audit Remediation Plan

Status: IN_EXECUTION  
Audit task: TEST-AUDIT-QUALITY-SCENARIO-V1.1-001

## Objective

Close the structural gap between technical/API regression and Product Manager Mandatory scenario acceptance.

## R0 — Test-analysis repair (completed)

- Product Scenario Inventory created: 8 Mandatory scenarios.
- Mandatory design mapping created at product-scenario level.
- V1.1 Expected frozen independently by Product Test Center.
- Browser execution flows defined.
- Existing API/TestClient evidence explicitly downgraded to cross-evidence where it is not a Browser Golden.
- #126/#127 Defect Regression Matrix created.
- R2 package is the unique current product candidate.

Result: Mandatory Scenario **design** coverage can reach 100%; execution coverage cannot be claimed yet.

## R1 — Product P0 blocker (open)

Gap: QS-AUD-P0-002.

Fact: the current Reverse Quality Web page can create/review ReverseQualityResult, and P01 can operate a Candidate, but there is no formal browser/user handoff from ReverseQualityResult to QualityScenario V1 Candidate. Existing engineering E2E uses the API to bridge this gap.

Required owner: Quality Scenario Product Manager + Development.

Required outcome:
- provide a formal user entry without creating a second workflow/state machine;
- use the existing formal V1 Candidate contract;
- preserve source/evidence and trigger_source/trigger_reason;
- do not auto-confirm or auto-publish;
- after handoff, user can continue in P01;
- no direct DB manipulation.

Because product code must change, **NEW_PRODUCT_RC_REQUIRED=YES**.

## R2 — Test baseline re-freeze after product fix

Test team action after new candidate delivery:
1. bind new ZIP + SHA;
2. rerun Package Dependency regression (#126);
3. rerun Missing Information Error Contract regression (#127);
4. freeze final V1.1 Manifest with exact test commit;
5. verify Expected and fixture hashes/versions unchanged unless a formally classified change is approved.

## R3 — Mandatory Browser E2E execution

Execute from clean state:
- BROWSER-GP-01 HIGH_PERCEPTION
- BROWSER-GP-02 RND_VALUE
- BROWSER-GP-03 Missing Information recovery
- BROWSER-GP-04 Reject terminal
- BROWSER-NEG-01 Evidence mismatch
- BROWSER-NEG-02 Provider/AI failure
- BROWSER-CONSUME-01 P02 query
- BROWSER-CONSUME-02 P03 detail/Evidence

Stop rule applies to each Golden chain independently.

## R4 — Gap closure

Close only when:
- P0_GAP_OPEN=0
- Mandatory Scenario Design Coverage=100%
- Mandatory Scenario Execution Coverage=100%
- Golden E2E Coverage=100%
- P01/P02/P03 Browser Covered=YES
- Mock boundary compliant
- Evidence complete
- #126/#127 regression traceable
- Test release unique

Then stop at:
`READY_FOR_TSE_AUDIT_REVIEW=YES`

The Quality Scenario Test Team must not write TEST_AUDIT_GATE=PASS.
