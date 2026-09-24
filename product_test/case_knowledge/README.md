# Case Knowledge Product Test Baseline V0.1

Owner: 产品测试中心 / 案例知识库测试团队

This directory freezes the deterministic product-test assets for the Product Manager scenarios of Unified Knowledge Production.

## Product scenarios covered
- External Source Golden A: NVMe Health and Linux UBI.
- Business Candidate Golden B: Historical Case -> Candidate -> Evaluation -> Review -> Publish -> Release -> Consumer.
- Evidence fail-closed.
- AI cannot self-confirm or self-publish.
- Provider 429 / malformed response fail-closed.
- Dual-entry Candidate Intake.
- Evaluation / Dedup / Conflict.
- Human CONFIRM / EDIT / REJECT and Publish Gate.
- Immutable Knowledge Release + Query/Evidence/Source Reference.
- Knowledge Processing UI.

## Mock boundary
Only the OpenAI-compatible Provider response is mocked. Runtime, KnowledgeExtractionService, schema validation, Candidate Store, Evidence binding, Evaluation, Review, Publish, Release, Query and consumer-facing contract run through the real implementation.

## Stable vs Real AI
The deterministic gate uses fixtures under `fixtures/openai`. Real Provider execution remains a supplemental smoke/compatibility gate via `tests/test_kp_d00_seed_real_provider.py`; it does not replace the frozen Mock gate.

## Execution
Linux/macOS: `./run_case_knowledge_product_test.sh`
Windows: `run_case_knowledge_product_test.bat`

Expected results are frozen in `expected/EXPECTED_RESULTS_CASE_KNOWLEDGE_V0.1.json`. Do not edit Expected or Fixture during execution to make a failure pass.
