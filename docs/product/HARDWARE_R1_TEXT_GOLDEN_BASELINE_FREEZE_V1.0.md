# Hardware R1 Text Golden Baseline Freeze V1.0

TASK=HARDWARE-R1-TEXT-GOLDEN-BASELINE-FREEZE-001  
OWNER=硬件案例库研发负责人  
SOURCE_PR=#322  
STATUS=FROZEN_FOR_MERGE_CLOSURE

## 1. Frozen implementation baseline

FROZEN_IMPLEMENTATION_SOURCE_COMMIT=`754cce29f74874ee9c89e2f5ed07a7edac0bda33`

This record freezes the Hardware R1 text Golden Knowledge path implemented on
PR #322. The freeze record itself may add documentation-only commits after the
implementation SHA above; those documentation commits do not redefine the
frozen implementation.

## 2. Golden Case gate

The following real field cases are the frozen R1 Text Golden set:

- A0152
- A0162
- A0207
- A0156

Gate ownership: System Engineer confirmation is accepted as the formal input to
this development freeze. Development does not re-run or redefine the field
Expected.

Frozen gate result:

```text
GOLDEN_CASE_COUNT=4
GOLDEN_CASE_GATE=PASS
EVIDENCE_GATE=PASS
FABRICATED_FACT_GATE=PASS
FABRICATED_BLOCK_ID_GATE=PASS
CACHE_SELECTIVE_RETRY_GATE=PASS
```

Real internal DOCX files and provider secrets remain company-local and MUST NOT
be committed to this repository.

## 3. Stage A freeze

```text
AGENT_ID=hardware_case.r1_case_extract
AGENT_CONFIG_VERSION=v1.5.0
PROMPT_VERSION=HC-R1-CASE-EXTRACT-V1.5.0
INPUT_CONTRACT=hardware-case-r1-agent-input/v3
OUTPUT_SCHEMA=HardwareCaseR1CaseExtractionV13/v1
VALIDATOR_VERSION=hardware-r1-stage-a-validator/v1
TIMEOUT_SECONDS=150
TRANSPORT_ATTEMPTS=2
MAX_PROVIDER_CALLS_PER_STEP=2
```

Frozen source identities at the implementation baseline:

```text
config/runtime/agents/hardware_case.r1_case_extract.yaml
blob=7db89c90b24b3498719532a3ec14e0103571a4a1

prompts/runtime/hardware_case/r1_case_extract_v1.md
blob=df41c9e4bc3abb15ba3e99ca92c3076b3badd038
```

Stage A semantic boundary remains source-grounded. In particular, process /
handling status is not Impact, measurable source-supported consequence is
preferred, and unsupported Impact remains MISSING.

## 4. Stage B freeze

```text
AGENT_ID=hardware_case.r1_reuse_derive
AGENT_CONFIG_VERSION=v1.5.1
PROMPT_VERSION=HC-R1-REUSE-DERIVE-V1.3.3.2
INPUT_CONTRACT=hardware-case-r1-reuse-input/v1
OUTPUT_SCHEMA=HardwareCaseR1ReusableKnowledgeV13/v1
VALIDATOR_VERSION=hardware-r1-stage-b-validator/v2
TIMEOUT_SECONDS=120
TRANSPORT_ATTEMPTS=2
MAX_PROVIDER_CALLS_PER_STEP=2
```

Frozen source identities at the implementation baseline:

```text
config/runtime/agents/hardware_case.r1_reuse_derive.yaml
blob=db2c011847f074b27fa83f7ea92e78d39b24fb4d

prompts/runtime/hardware_case/r1_reuse_derive_v1.md
blob=afcb2a03854436709d7feea91ec8d0c47473d68b
```

SOURCE-GROUNDED TOPOLOGY is part of the frozen Stage B semantic Contract.
Engineering plausibility is not source evidence. Unsupported topology,
connection nodes, pin relationships, component placement, or wiring details
must not be reconstructed from convention or from unavailable/redacted image
content.

## 5. Pipeline / cache / Knowledge Model freeze

```text
PIPELINE_VERSION=hardware-r1-agent-pipeline/v1.3.3
EXTRACTION_CONTRACT=hardware-r1-extraction/v2
PIPELINE_RESULT_VERSION=hardware-case-r1-agent-result/v3
EXECUTION_TRACE_VERSION=hardware-r1-execution-trace/v1.5
CACHE_KEY_VERSION=v2
KNOWLEDGE_OBJECT_CONTRACT=hardware-case-knowledge-object/v1
OBJECT_STATUS=CANDIDATE_ONLY
FORMAL_KNOWLEDGE_WRITE=NO
```

Frozen implementation source identities:

```text
services/hardware_case_markdown_agent.py
blob=62be8d1170ed35e1b959467d1a1dbb53f06835b2

services/hardware_case_r1_runtime.py
blob=5c2f401487acc17064110888304b85a2be983ffe
```

Cache semantics remain:

- only Runtime/Schema PASS + current local validation PASS may commit Success Cache;
- Stage A and Stage B cache identities are independent;
- cache hit is revalidated by the current validator;
- invalid cache is rejected/evicted and may recover through Provider;
- Retry Failed Stage reruns only the failed stage when its prerequisite Last
  Good input remains valid;
- failed retry never overwrites Last Good;
- Run / Resume may complete with zero Provider calls on valid Stage A + Stage B
  cache hits;
- Preview History, Stage Cache, and Runtime Audit remain separate lifecycles.

## 6. Frozen field-validation candidate

```text
PACKAGE=HARDWARE_CASE_PRODUCT_TEST_FULL_R1_754cce29f748.zip
PACKAGE_SHA256=1567d99c60426c8cc408381b9dbca99fc5b286cc1a459edf54904a4d1f25fa4d
CI_RUN_ID=37034588237
CI_ARTIFACT_ID=11238828609
PRODUCT_TEST_PACKAGE=PASS
MANIFEST=PASS
STARTUP_IMPORT=PASS
```

This package identity is a FIELD VALIDATION CANDIDATE, not a Release claim.

## 7. PR #322 scope lock

From this freeze point, PR #322 is closure-only.

```text
NEW_BATCH_FUNCTION=FORBIDDEN
NEW_SOURCE_FUNCTION=FORBIDDEN
NEW_CONSUMPTION_FUNCTION=FORBIDDEN
NEW_KNOWLEDGE_FIELD=FORBIDDEN
PIPELINE_REDESIGN=FORBIDDEN
MODEL_CHANGE=FORBIDDEN
PROMPT_SEMANTIC_EXPANSION=FORBIDDEN
SCHEMA_EXPANSION=FORBIDDEN
AUTO_PUBLISH=FORBIDDEN
FORMAL_KNOWLEDGE_WRITE=FORBIDDEN
```

Any future Batch / Source / Consumption capability, or any semantic change to
Stage A, Stage B, validator, schema, cache identity, or Knowledge Object, must
start from a new task and versioned change after #322 merge closure.

## 8. Merge-closure target

```text
4_CASE_PASS=YES
EVIDENCE_GATE_PASS=YES
CACHE_SELECTIVE_RETRY_PASS=YES
KNOWLEDGE_MODEL_FREEZE=YES
SCOPE_FREEZE=YES
TARGET=#322_READY_FOR_MERGE_CLOSURE
RELEASE_GATE=NOT_CLAIMED
```
