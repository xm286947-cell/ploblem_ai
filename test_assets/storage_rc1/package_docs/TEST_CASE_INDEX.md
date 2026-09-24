# Storage RC1 Test Case Index

Status: FROZEN_FOR_WORK_EXECUTION
Product baseline: PRODUCT_REQUIREMENT_BASELINE_STORAGE_MVP_RC1_V1.0
Expected baseline: STORAGE_EXPECTED_BASELINE_RC1_V0.1

## Case sets

- Interface: ITF-001..ITF-032
  - Drive baseline: STORAGE_INTERFACE_TEST_CASES_RC1_V0.1
- Integration: INT-01..INT-12
  - Drive baseline: STORAGE_INTEGRATION_TEST_CASES_RC1_V0.1
- System: SYS-001..SYS-015
  - Drive baseline: STORAGE_SYSTEM_TEST_CASES_RC1_V0.1
- OpenAI Mock: M01..M22
  - Code fixtures: test_assets/storage_rc1/fixtures/
  - Drive spec: STORAGE_OPENAI_MOCK_FIXTURE_SPEC_RC1_V0.1
- Requirement coverage: REQ-STG-001..REQ-STG-015
  - Code mapping: test_assets/storage_rc1/scenario_catalog.json
- Golden paths:
  - GOLDEN-A Datasheet -> Confirmed Device Fact
  - GOLDEN-B Knowledge Production -> Formal Release -> Storage Consumer
  - GOLDEN-C Compare -> Diagnose -> Change Impact -> Evidence

## Frozen rules

1. Work executes; Work does not design tests.
2. Work must not modify test code, Expected, Mock Fixture or SUT business code.
3. Mock replaces Provider/AI Response only.
4. Repository/DB/Review/Publish/Query/Consumer remain real.
5. AI Candidate is not Formal Device Fact.
6. AI has no Confirm or Publish authority.
7. P0 FAIL/BLOCKED/NOT_TESTED prevents Product Test Gate PASS.
