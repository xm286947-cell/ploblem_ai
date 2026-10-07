# Hardware AI Retrieval W1 — Retrieval Metadata + AI Tagger

TASK=HARDWARE-AI-RETRIEVAL-W1-RETRIEVAL-METADATA-TAGGER-001
STATUS=W1A_PASS
BASE_MERGE_COMMIT=107d5d3f34f3e9f25fb35d6bfa8228c4e3317629
BRANCH=feature/hardware-ai-retrieval-w1-metadata-tagger
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

W1A_EVIDENCE_RUN=37574480796\nW1A_EVIDENCE_JOB=112640190622\nW1A_TEST_RESULT=13 passed in 0.30s\n\nW1B_RUNTIME_BINDING=NOT_STARTED
W1B_REAL_PROVIDER_TAGGING=NOT_STARTED
W1C_OPENSEARCH_INDEX_DOCUMENT=NOT_STARTED

W1_PASS=NO
