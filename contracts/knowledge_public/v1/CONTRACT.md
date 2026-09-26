# Unified Knowledge Public Contract V1

Status: FROZEN_FOR_HARDWARE_CASE_P0

This contract is a public facade over the existing Knowledge Production core.
It is domain-agnostic: Hardware Case structured content is opaque JSON and is
never interpreted by the Knowledge Platform.

## Contract versions

- Candidate: `knowledge-candidate/v1`
- Evidence: `knowledge-evidence/v1`
- Review: `knowledge-review/v1`
- Publish: `knowledge-publish/v1`
- Query: `knowledge-query/v1`
- Published object: `knowledge-object/v1`

## Candidate Intake

Public entry: `POST /v1/knowledge/candidates`

Required request fields:

- `candidate_id`
- `source_document_id`
- `domain`
- `object_type`
- `structured_content`
- `evidence_refs`
- `status=PENDING_REVIEW`
- `created_at`
- `revision`
- `contract_version=knowledge-candidate/v1`

`structured_content` is an opaque business schema. The platform MUST NOT
interpret Hardware Case fields. Candidate Intake is idempotent by
`candidate_id + canonical payload`. Reusing the same id with different
content fails with `CANDIDATE_ID_CONFLICT`.

## Evidence

Public entry: `POST /v1/knowledge/evidences`

Evidence binds a business source such as a Word document to a controlled source
reference and optional excerpt. Required identity fields are
`evidence_id`, `source_document_id`, `domain`, `source_type`,
`source_ref`, `content_hash`, and `revision`. Location can include
`page`, `section`, and `paragraph`.

If `source_text` is supplied, SHA256 MUST equal `content_hash`; otherwise
the request fails with `EVIDENCE_CONTENT_HASH_MISMATCH`. Evidence Intake is
idempotent by `evidence_id + canonical payload`; conflicting reuse fails with
`EVIDENCE_ID_CONFLICT`.

## Human Review

Public entry: `POST /v1/knowledge/reviews`

Actions:

- `CONFIRM`
- `EDIT`
- `REJECT`

The audit record contains `reviewer`, `review_time`, `review_comment`,
`confirmed_value/reviewed_content`, and `revision`. Human-confirmed content
is stored separately from the original AI Candidate. `EDIT` requires
`reviewed_content`.

## Publish

Public entry: `POST /v1/knowledge/publish`

Publish is fail-closed:

```
Candidate
+ required Evidence
+ latest Human Review = CONFIRMED
+ existing Knowledge Production publish gates
-> Published Knowledge Object
```

An unreviewed or rejected Candidate fails with `PUBLISH_NOT_CONFIRMED`.

Publish requires `idempotency_key`. Repeating the same key for the same
Candidate returns the same object. Reusing a key for another Candidate fails
with `IDEMPOTENCY_KEY_CONFLICT`.

Revision semantics reuse the existing immutable Knowledge Object
`object_version` history. The public `revision` field carries the business
Candidate revision; Knowledge Object history remains independently versioned.

## Published Knowledge Object

The public representation contains:

- `knowledge_id`
- `domain`
- `object_type`
- `content` (opaque business JSON)
- `candidate_ref`
- `evidence_refs`
- `revision`
- `published_at`
- `status`
- `knowledge_release_version` when queried from a Release

## Query and Evidence Resolve

- `POST /v1/knowledge/search`
- `GET /v1/knowledge/objects/{knowledge_id}?knowledge_release_version=...`
- `GET /v1/knowledge/evidences/{evidence_id}?knowledge_release_version=...`

`POST /v1/knowledge/search` also accepts the optional `candidate_refs` array.
When supplied, it selects published objects whose public `candidate_ref`
matches one of those values. This is an additive `knowledge-query/v1`
extension; callers that omit it retain the existing query behavior. Business
consumers can use their stable, versioned candidate reference to resolve a
Publication without using the Knowledge object's internal `knowledge_id`.
Hardware Case uses `HC-KNOWLEDGE-{case_id}-R{revision}` as this Public Ref;
the matching published object's `candidate_ref` is the binding back to that
Public Ref. Its `evidence_refs` are then resolved through the Evidence
endpoint above.

Consumer access is pinned to an immutable Knowledge Release. Hardware Case MUST
NOT read the Knowledge repository or database directly.

## Failure semantics

Stable public failures include:

- `CANDIDATE_CONTRACT_INVALID`
- `CANDIDATE_ID_CONFLICT`
- `EVIDENCE_CONTRACT_INVALID`
- `EVIDENCE_CONTENT_HASH_MISMATCH`
- `EVIDENCE_ID_CONFLICT`
- `EVIDENCE_MISSING`
- `REVIEW_CONTRACT_INVALID`
- `CANDIDATE_NOT_REVIEW_READY`
- `PUBLISH_CONTRACT_INVALID`
- `PUBLISH_NOT_CONFIRMED`
- `IDEMPOTENCY_KEY_CONFLICT`
- `KNOWLEDGE_RELEASE_NOT_FOUND`
- `KNOWLEDGE_OBJECT_NOT_FOUND`
- `EVIDENCE_NOT_FOUND`

These are business/contract failures and are non-retryable unless a caller
changes the invalid/missing input or the referenced Release becomes available.
