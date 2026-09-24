# QUALITY SCENARIO RC1 — Product Test Plan V0.1

Status: TEST_BASELINE_READY  
Owner: Product Test Center / Quality Scenario Test Team  
Product baseline: REQUIREMENT_BASELINE_质量场景库_MVP_V0.2 / FROZEN_FOR_RC1  
Code baseline: feature/reverse-quality-v01@6ad93f1396585163fffd1e4a70a677aee917a6ff  
Test branch: test/quality-scenario-rc1-baseline-v01

## Objective

Verify the frozen RC1 Golden Path:

Problem Fact → Reverse Quality / Unified Runtime → OpenAI-compatible Provider → ReverseQualityResult → Candidate V1 → Human Review/Edit → Quality + R&D confirmation → Publish → P02 Library → P03 Detail/History → Evidence Traceability → Source reverse lookup.

## Test ownership boundary

Product Test Center owns test cases, Mock AI JSON, Expected, coverage, automation and gate judgement. Development owns fixes only. Work executes the frozen manifest only and must not change tests, fixtures, Expected, or product code during execution.

## Mock boundary

Only Provider / AI Response is mocked. These remain real:
- ReverseQualityRuntimeExecutor
- Reverse Quality business validation
- Candidate Adapter
- QualityScenario V1 schema
- SQLite repositories
- Review / Confirm / Reject / Publish
- P02 query
- P03 detail/history
- Evidence traceability
- Source reverse lookup

## Layers

Interface: API required inputs, status/state transitions, version conflict, 404, idempotency.  
Integration: Runtime → Mock Provider → Reverse Quality → Candidate → workflow → traceability.  
System: frozen single Golden Path, two trigger sources, three pages, blocker/fail-closed, reject terminal path.  
Automation: pytest, deterministic fixtures and Expected.

## Frozen cases

QS-PT-001 Provider contract + Runtime + secret safety  
QS-PT-002 HIGH_PERCEPTION / RND_VALUE same workflow  
QS-PT-003 missing_information blocks confirmation  
QS-PT-004 invalid AI outputs fail closed  
QS-PT-005 API required-input contract  
QS-PT-006 optimistic concurrency / stale write  
QS-PT-007 one AI call through publish + idempotent publish  
QS-PT-008 Evidence traceability + source reverse lookup  
QS-PT-009 P02 query + P03 detail/history + 404  
QS-PT-010 429 provider fault  
QS-PT-011 three product pages / no approval-state expansion  
QS-PT-012 Reject terminal path

## Exit criteria

TEST_BASELINE_READY means the test design and assets are frozen. It does not mean execution PASS. Formal execution must use TEST_EXECUTION_MANIFEST.json and return Evidence before any PRODUCT_TEST_GATE decision.
