# STORAGE-SEMANTIC-SECOND-PASS-001

Status: DEVELOPMENT / Storage integration candidate

## Goal

When the primary Storage extraction terminates with
`SEMANTIC_REPAIR_REQUIRED` and Runtime exposes a controlled semantic
handoff, Storage may perform exactly one business-owned second extraction
against the first Provider-returned material.

This is a Storage business behavior. Runtime remains unchanged.

## Trigger

Second pass is eligible only when all conditions are true:

- primary Runtime result is not COMPLETED;
- error code is exactly `SEMANTIC_REPAIR_REQUIRED`;
- `recoverable_content_available=true`;
- `content_ref`, `content_hash` and `content_length` are present;
- `raw_finish_reason != length`.

`OUTPUT_TRUNCATED` is not treated as eligible business material.

## Controlled handoff read

Storage reads the first semantic handoff through Runtime using both
`task_id` and `content_ref`.

Before any second Provider call, Storage verifies:

- content length equals `content_length`;
- SHA-256 equals `content_hash`.

If verification fails, second pass is not called.

## Second-pass input boundary

The second Provider call receives only:

```json
{
  "device_type": "eMMC",
  "parameter_scope": "lifetime",
  "required_fields": ["pe_cycle"],
  "provider_material": "<first Provider semantic handoff content>"
}
```

The original `source_text` / PDF content is deliberately not forwarded to
the second pass.

The semantic re-extraction prompt treats `provider_material` as untrusted
data, not instructions.

## Second-pass agent

Agent:

```text
storage.emmc.semantic_reextract
```

Config:

```text
config/runtime/agents/storage.emmc.semantic_reextract.yaml
```

Prompt:

```text
prompts/runtime/storage/emmc_semantic_reextract.md
```

The second pass uses the same `StorageFieldResult` output contract and strict
Runtime JSON/schema gate.

## No recursion

Storage allows only one business semantic second pass.

Runtime may still perform its configured transport/validation retries inside
that second Agent invocation. If the second invocation terminates in failure,
Storage returns `SECOND_PASS_FAILED` and does not invoke a third business
pass.

## Result semantics

For V0.1 there is no merge with an invalid primary structured result because
the primary result never passed the strict JSON/schema gate.

Therefore:

- primary COMPLETED -> use primary result;
- primary semantic failure + second pass COMPLETED -> second-pass result is the
  replacement candidate;
- second-pass field set must exactly match `required_fields`;
- missing data must be represented by a `MISSING` Storage field item;
- any field-set mismatch is rejected;
- failed second pass produces no reviewed specification.

## Runtime / Business boundary

Runtime owns:

- Provider execution;
- strict JSON + schema validation;
- deterministic wrapper recovery;
- semantic handoff storage/read;
- retry/budget/evidence.

Storage owns:

- whether a semantic handoff is eligible for second analysis;
- the second-pass Prompt;
- required field semantics;
- field-set completeness gate;
- whether the second-pass output is accepted.

Storage does not introduce loose JSON parsing, regex repair, or Provider
transport code.
