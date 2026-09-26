# SOURCE_PROBLEM_ITR_REF_CONTRACT_V1.0

Contract version: `source-problem-itr-ref/v1`

## 1. Purpose

This contract gives Existing Problem / ITR data one stable public reference
that Major Case, Historical Case, Repeat Risk, and `major-problem-context/v1`
can share without depending on storage implementation details.

## 2. Public Ref Definition

`PUBLIC_REF_TYPE=ITR`. The public reference is the canonical business ITR
number, not a database or source-record identity:

```text
public_ref = canonical_itr = normalize_itr(source_business_key)
```

`event_id`, `source_link_id`, `material_id`, `record_id`, `case_id`, document
IDs, version IDs, titles, and display names are not public identity fields.

## 3. Canonicalization

Every public entry point uses the single `normalize_itr()` implementation in
`quality_knowledge.problem_refs`. It removes whitespace, uppercases the key,
and returns the canonical ITR spelling. A valid public ref starts with `ITR`
and has at least one business-key character after it.

## 4. ITR / ITR-CS Compatibility

The legacy suffix form is accepted without creating a second identity:

```text
ITR001CS -> ITR001
```

## 5. Stable Identity Rule

Title, description, status, owner, product, customer, industry, source version,
Major Case revisions, and repository IDs may change without changing the
public ref. Only the normalized business ITR key determines identity.

## 6. Internal ID Boundary

`record_id` remains available inside source evidence and trace processing. It
is never returned as `public_ref`, `canonical_itr`, or `problem_id`. Consumers
receive the public DTO, not a repository or database object.

## 7. Resolve Semantics

`SourceProblemItrRefResolver` validates and normalizes a supplied business key.
An injected public lookup may resolve it to `source_status` and a canonical
`source_refs` list. The resolver does not know or expose repository classes.

## 8. Invalid / Not Found Semantics

Empty, whitespace-only, or non-ITR input raises `INVALID_REF`. A valid
canonical ref with no source match raises
`REF_VALID_BUT_SOURCE_NOT_FOUND`. No UUID, event ID, record ID, case ID, or
title is used as a fallback.

## 9. Source Trace

The stable trace is:

```text
public_ref -> Existing Problem / ITR source -> source evidence
```

Internal trace fields such as `standard_itr`, `source_type`, `record_id`,
`source_group`, and `source_version` remain implementation details.

## 10. Historical Compatibility

Existing Major repository rows continue to use `standard_itr` internally.
Writes and public lookups normalize that value, so existing `ITR` and `ITR-CS`
forms resolve to the same public identity. Historical Case and Repeat Risk
consumers continue to receive their existing business ITR value.

## 11. major-problem-context Binding

For the same source problem:

```text
source-problem-itr-ref/v1.public_ref
== major-problem-context/v1.problem_id
== major-problem-context/v1.source_refs[0]
```

The context provider emits only the normalized public reference.

## 12. Consumer Rules

Consumers must depend on `contract_version`, `ref_type`, `public_ref`,
`canonical_itr`, `source_status`, and `source_refs`. Consumers must not query
Major SQLite tables or import `MajorKnowledgeRepository`, a material
repository, Quality Scenario, or Unified Knowledge internals.

## 13. Version Compatibility

`source-problem-itr-ref/v1` is additive and stable. New fields require a new
contract version or an explicitly compatible extension. Existing public ref
semantics must not be changed in place.

## 14. R01-R08 Gate

The independent contract test covers canonical normalization, ITR-CS
compatibility, stable identity, internal-ID isolation, invalid refs, valid-but-
not-found refs, binding to `major-problem-context/v1`, and the public boundary
constraints. No new table, master data, cross-domain SQL, repository exposure,
or second normalizer is part of this contract.
