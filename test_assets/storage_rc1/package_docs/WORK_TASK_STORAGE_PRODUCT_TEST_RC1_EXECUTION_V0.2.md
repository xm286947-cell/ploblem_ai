# WORK TASK | Storage Product Test RC1 Execution V0.2

Priority: P0
Owner: Product Test Center / Storage Test Team / TSE
Execution role: ChatGPT Work
Status: READY_FOR_WORK_EXECUTION_AFTER_PACKAGE_INTEGRITY_PASS

## Unique inputs

1. SUT: STORAGE_PRODUCT_MVP_RC1_JOINT_PACKAGE_20260924.zip
   SHA256: b6f48ad5447ad82dd74ccbe31ff650c4f0eb6b64791e65812c0e19dc1c529017
   Note: this is the frozen current SUT input for execution, not a pre-declared Product Gate PASS package.
2. Frozen Test Package: STORAGE_PRODUCT_TEST_BASELINE_RC1_V0.3.zip
   SHA256: use the companion .sha256 file from the package workflow.
3. Manifest: test_assets/storage_rc1/package_docs/TEST_EXECUTION_MANIFEST_RC1_V0.3.json
4. Browser checklist: test_assets/storage_rc1/package_docs/BROWSER_SYSTEM_CHECKLIST.md

## Hard boundaries

Work only executes frozen tests and collects Evidence.
Work MUST NOT:
- modify SUT business code;
- modify test code;
- modify Expected;
- modify Mock Fixture;
- skip FAIL to obtain PASS;
- reinterpret product requirements;
- use real internal production data;
- decide Product Test Gate on behalf of TSE.

If a test harness itself conflicts with the frozen product requirement, return TEST_HARNESS_GAP with Evidence. Do not patch it during execution.

## Execution order

P0 Package integrity and baseline
P1 Test asset gate
P2 Shared OpenAI Mock / Runtime
P3 Provider 429
P4 Provider timeout
P5 JSON truncation
P6 Semantic repair / Fail-Closed
P7 Browser Golden A
P8 Browser Golden B
P9 Browser Golden C
P10 REQ-STG-015 Chinese UI

## Mandatory browser coverage

- Golden A: Datasheet -> Confirmed Device Fact
- Golden B: Knowledge Production -> Formal Release -> Storage Consumer
- Golden C: Compare -> Diagnose -> Change Impact -> Evidence
- REQ-STG-015: P02-P08 Chinese UI and parameter naming

## Result

Return exactly the fields in RESULT_RETURN_TEMPLATE.md plus evidence paths.
Any P0 FAIL/BLOCKED/NOT_TESTED prevents FINAL_TEST_GATE=PASS.
