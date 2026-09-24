# Storage RC1 Browser System Checklist

Status: FROZEN_FOR_WORK_EXECUTION

For every case record: RESULT, timestamp, URL, screenshot/evidence path, observed state, defect candidate.

## P0 Golden Paths

### SYS-001 / GOLDEN-A
1. Open product from ACCEPTANCE_BASE_URL.
2. Upload only Synthetic/authorized test datasheet.
3. Verify Identity Candidate appears and is not auto-confirmed.
4. Edit one identity field and Confirm.
5. Run parameter recognition.
6. Verify KEY_SPEC / KEY_DIAGNOSTIC / COMPREHENSIVE sections.
7. Verify Coverage state is explicit and separate from Review status.
8. Open Evidence.
9. Perform Confirm, Edit+Confirm and Reject on different candidates.
10. Refresh/re-enter and verify persistence.
11. Search device in Device Library and open Detail.
Expected: full chain closes; rejected value is not formal downstream fact; Evidence is traceable.

### SYS-007 / GOLDEN-B
1. Enter formal Knowledge Source.
2. Run Mock AI extraction.
3. Verify Candidate + Evidence.
4. Human Review.
5. Publish.
6. Verify Formal Release/version.
7. Storage consumes the fixed Release through formal contract.
8. Query from product page and open Evidence.
Expected: AI cannot publish; Storage does not consume Candidate DB; Release version and Evidence visible.

### SYS-013 / GOLDEN-C
1. Select two devices with Confirmed Facts.
2. Compare and enable only-difference / only-missing.
3. Open cell Evidence.
4. Run one lifetime diagnostic.
5. Run one change-impact analysis.
6. Open Fact/Knowledge Evidence.
Expected: same Parameter Schema; no fabricated runtime value; uncertainty becomes validation items; no automatic replacement decision.

## Requirement/system cases

- SYS-002 upload failure: clear Chinese error; no consumable asset.
- SYS-003 identity confirmation: wrong Mock value can be edited and confirmed; AI value preserved.
- SYS-004 parameter/Coverage: FOUND, NOT_FOUND, NOT_APPLICABLE, NOT_CHECKED, AMBIGUOUS all visible; no '-' flattening.
- SYS-005 review closure: Confirm/Edit+Confirm/Reject persist after refresh/restart.
- SYS-006 device library/detail/evidence: filter/search/detail/evidence path works.
- SYS-008 compare: 2-4 devices, same schema, coverage semantics preserved.
- SYS-009 diagnostic: answers what/where/how/judgement/evidence; no runtime value fabrication.
- SYS-010 impact: Fact vs Analysis separated; uncertain facts become validation items.
- SYS-011 maintenance/consumption separation: normal consumer is not forced through maintenance flow.
- SYS-012 REQ-STG-015: P02-P08 titles/buttons/status/prompts/errors/empty states are Chinese; parameter names use Chinese (English Name).
- SYS-014 invalid AI JSON: understandable failure/pending state; no fabricated formal fact; prior valid data preserved.
- SYS-015 missing Evidence: cannot be treated as verified formal fact; confirm/publish gate blocks or explicitly marks unverifiable.

## Gate

GOLDEN-A, GOLDEN-B and GOLDEN-C are P0. Any FAIL/BLOCKED/NOT_TESTED => FINAL_TEST_GATE != PASS.
