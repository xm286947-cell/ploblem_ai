# TEST_PLAN_CASE_KNOWLEDGE_MVP_V0.1

Status: TEST_BASELINE_READY_CANDIDATE
Owner: Case Knowledge Test Team
Product input: REQ_统一知识库_知识生产与候选知识接收闭环_V0.1 + Knowledge Production MVP V0.2.

## Objective
Verify that source documents and business discoveries become formal Knowledge Objects only through evidence-backed evaluation, human review, fail-closed publish, immutable release and version-pinned query/consumer contracts.

## Layers
INTERFACE: Candidate Intake, Query, evidence/version/contract validation.
INTEGRATION: Runtime -> OpenAI Mock -> extraction -> candidate -> evidence -> evaluation -> review -> publish -> release -> query.
SYSTEM: NVMe/UBI Golden A, Historical Case Golden B, Processing UI.
AUTOMATION: frozen pytest suite and deterministic fixtures.

## P0 invariants
1. AI output is never CONFIRMED/PUBLISHED.
2. No valid evidence => no formal knowledge.
3. Duplicate/conflict is not silently merged.
4. Human EDIT/CONFIRM/REJECT is auditable.
5. Published/released knowledge is version controlled.
6. Query returns formal Knowledge Object + Evidence + Source Reference; raw chunk is not the business contract.
7. Historical Case remains authoritative; Knowledge stores stable provenance/evidence refs rather than copying the case database.
8. Provider failures never create candidate/published side effects.
