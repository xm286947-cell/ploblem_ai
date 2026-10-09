# Hardware AI Retrieval W1 — Retrieval Metadata + AI Tagger

TASK=HARDWARE-AI-RETRIEVAL-W1-RETRIEVAL-METADATA-TAGGER-001
STATUS=W1D_DEVELOPMENT
BASE_MERGE_COMMIT=107d5d3f34f3e9f25fb35d6bfa8228c4e3317629
BRANCH=feature/hardware-ai-retrieval-w1d-query-why-hit
ARCH_BASELINE=HARDWARE_KNOWLEDGE_AI_RETRIEVAL_SYSTEM_ARCHITECTURE_V0.1

## Goal

Generate rebuildable retrieval metadata from the existing
`hardware-knowledge-consumption/v1` read projection without changing Formal
Knowledge, Stage A/B, Unified Knowledge, Runtime, or the current Hardware R1 E2E.

The AI tagger is advisory.  Its output is untrusted until the W1 validator
accepts it.

## Reuse decision

W1 does not parse Formal Knowledge again.

It reuses the existing read projection in
`services/hardware_knowledge_consumption.py`, which already resolves verified
Formal Knowledge through the frozen public contract and exposes fields such as:

- title / symptom / occurrence_condition / failure_mode
- root_cause / failure_mechanism / analysis_process
- actions / verification_result
- engineering_rule / design_constraint / diagnostic_clue
- verification_method / applicability / conclusion
- interface / signal / key_parameters / device_refs

This preserves Formal Knowledge as the source of truth.

## Contract

Output:

`hardware-retrieval-metadata/v1`

Identity and provenance:

- knowledge_id
- source contract = hardware-knowledge-consumption/v1
- projection_schema_version
- formal_revision
- formal_object_hash

Scope is deterministic and explicit-only:

- business_case_id
- source_domain
- source_object_type
- interfaces
- signals
- devices

Tags are split into three non-interchangeable classes.

### FACT

A literal phrase already present in the referenced Formal-derived source field.

Rules:

- source_term must exist literally in an allowlisted source field
- term must equal source_term after NFKC/case/whitespace normalization
- derived=false
- claim_safe=true
- usage=RANK_AND_EXPLAIN

### NORMALIZED

Mechanical normalization only.  No semantic aliasing.

Allowed change examples:

- case
- underscore / hyphen / whitespace
- Unicode compatibility normalization

Rules:

- source_term must exist literally in an allowlisted source field
- term and source_term must be mechanically equivalent
- derived=false
- claim_safe=true
- usage=RANK_AND_EXPLAIN

Synonyms are not NORMALIZED.

### EXPANSION

AI-derived wording used only to increase recall.

Rules:

- every expansion must declare a literal source_term anchor
- source_term must exist in the referenced Formal-derived field
- semantic equivalence is not treated as a fact
- derived=true
- claim_safe=false
- usage=RECALL_ONLY

EXPANSION must never become a structured filter or be presented as a Formal
Knowledge claim.

## W1A implementation

- services/hardware_retrieval_metadata.py
- services/hardware_retrieval_tagger.py
- tests/test_hardware_retrieval_metadata.py
- .github/workflows/hardware-ai-retrieval-w1.yml

The provider-neutral tagger passes only allowlisted Formal-derived values to an
injected model invoker.  Existing Runtime/Provider code is not modified.

## W1 isolation

FORMAL_KNOWLEDGE_CHANGE=NO
STAGE_A_CHANGE=NO
STAGE_B_CHANGE=NO
KNOWLEDGE_SCHEMA_CHANGE=NO
UNIFIED_KNOWLEDGE_CONTRACT_CHANGE=NO
CURRENT_CONSUMPTION_CONTRACT_CHANGE=NO
CURRENT_SEARCH_UI_CHANGE=NO
CURRENT_FINAL_E2E_CHANGE=NO
RUNTIME_CHANGE=NO
VECTOR=NO
EMBEDDING=NO
RAG=NO

## W1 gates

W1A_METADATA_CONTRACT=PASS
W1A_FACT_FAIL_CLOSED=PASS
W1A_NORMALIZED_NO_NEW_FACT=PASS
W1A_EXPANSION_RECALL_ONLY=PASS
W1A_DETERMINISTIC_REBUILD=PASS
W1A_UNIT_TEST=PASS

W1A_EVIDENCE_RUN=37574480796
W1A_EVIDENCE_JOB=112640190622
W1A_TEST_RESULT=13 passed in 0.30s

W1B_RUNTIME_BINDING=PASS
W1B_REAL_AGENT_VALIDATION=CODEX_MANUAL_HARNESS_ONLY
W1B_TARGETED_CI=17 passed
W1B_RUNTIME_CORE_CHANGE=NO
W1B_PROVIDER_CONFIG=HARDWARE_CASE_MODEL_CONFIG_REUSE
W1B_CI_PROVIDER_MODE=MOCK_ONLY
W1C_OPENSEARCH_INDEX_DOCUMENT=IN_PROGRESS

W1_PASS=NO


## W1B Runtime Binding Progress

W1B_CODE_STATUS=PASS
W1B_HEAD_BEFORE_ENV_CLOSURE=4c2794ff066751483e2208f31f851b1c38cc41d5
W1B_TARGETED_RUN=37574949564
W1B_TARGETED_JOB=112641635376
W1B_TARGETED_RESULT=17 passed in 0.93s
RUNTIME_FOUR_DOMAIN_BINDING_RUN=37574949562
RUNTIME_FOUR_DOMAIN_BINDING=PASS

W1B_UNIFIED_RUNTIME_REUSE=PASS
W1B_STRUCTURED_SCHEMA=PASS
W1B_SECRET_NON_PERSISTENCE=PASS
W1B_IDEMPOTENCY=PASS
W1B_VALIDATOR_CHAIN=PASS
RUNTIME_CORE_CHANGE=NO
RUNTIME_CONTRACT_CHANGE=NO

W1B_REAL_PROVIDER_CI=REMOVED
W1B_REAL_AGENT_VALIDATION=CODEX_MANUAL_HARNESS_ONLY
W1B_REAL_AGENT_HARNESS=tools/hardware_retrieval_real_agent_validation.py
W1B_REAL_AGENT_AUTHORIZATION=--execute-real_REQUIRED
W1B_REAL_AGENT_RUN=NOT_RUN_BY_CI
W1B_REAL_AGENT_SAMPLE_INPUT=tests/fixtures/hardware_retrieval_real_agent_cases.json
W1B_AGENT_DEFINITION=INDEPENDENT
W1B_PROVIDER_CONFIG=HARDWARE_CASE_MODEL_CONFIG_REUSE

The retrieval Agent, Prompt, and structured output Schema remain independent.
Provider/model selection reuses the existing Hardware Case model configuration
path; CI remains Mock / Contract / Regression only. The explicit Codex harness
accepts a JSON array of Formal-derived `hardware-knowledge-consumption/v1`
projections and refuses to construct Runtime unless `--execute-real` is given.
Three synthetic engineering examples (MCU RESET_N reset, CAN interruption, and
LDO output oscillation) are provided as harness input fixtures; they are not
Formal Knowledge records or production data.
This correction prepares the manual validation path but does not execute a
real Agent or Provider call.

W1_PASS=NO
W1C_OPENSEARCH_INDEX_DOCUMENT=IN_PROGRESS
NEXT=W1C_TARGETED_CI


## W1C OpenSearch Index Document

W1B_MERGE_COMMIT=3e93ae1428ed7ee79301f483ee54078144747166
W1C_BRANCH=feature/hardware-ai-retrieval-w1c-opensearch-document
W1C_INDEX_CONTRACT=hardware-retrieval-index-document/v1
W1C_INDEX_SCHEMA_VERSION=1

W1C_REUSE_W0_HTTP_ADAPTER=YES
W1C_SECOND_SEARCH_CLIENT=NO
W1C_FORMAL_WRITEBACK=NO

Index separation is mandatory:

- search_text = Formal-derived values + FACT/NORMALIZED tags
- recall_text = search_text + EXPANSION tags
- structured scope filters = explicit Formal-derived interface/signal/device values only
- EXPANSION is never copied into scope filters
- tag_provenance preserves source_term/source_fields/derived/claim_safe for later WHY_HIT

W1C_SEARCH_TEXT_CLAIM_SAFE=YES
W1C_EXPANSION_RECALL_ONLY=YES
W1C_STRUCTURED_FILTER_FROM_EXPANSION=FORBIDDEN
W1C_WHY_HIT_PROVENANCE_READY=YES

W1C_TARGETED_CI=25 passed
W1_PASS=NO


## W1D BM25 + Filter + WHY_HIT

W1C_MERGE_COMMIT=e6faffd586bdabf7049f7797879a00d353827f54
W1D_BRANCH=feature/hardware-ai-retrieval-w1d-query-why-hit

W1D_REUSE_W0_SEARCH_ADAPTER=YES
W1D_SEARCH_FIELDS=search_text^4 + recall_text
W1D_MANDATORY_FILTERS=formal_status/source_domain/source_object_type
W1D_USER_FILTERS=business_case_id/interface/signal/device

WHY_HIT rules:
- Formal source field / FACT / NORMALIZED => claim_safe=true
- EXPANSION => reason_type=EXPANSION_RECALL, claim_safe=false
- filter-only retrieval creates no fabricated reason
- unknown filters fail closed
- malformed index hits fail closed

W1D_BM25_FILTER_WHY_HIT=IN_PROGRESS
W1D_EXPANSION_AS_FORMAL_REASON=FORBIDDEN
W1D_TARGETED_CI=PENDING
W1_PASS=NO
