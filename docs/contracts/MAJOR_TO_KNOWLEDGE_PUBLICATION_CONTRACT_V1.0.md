# MAJOR_TO_KNOWLEDGE_PUBLICATION_CONTRACT_V1.0

Status: **FROZEN**

Contract version: `major-knowledge-publication/v1`

This is the only public hand-off from the Major Issue domain to Unified
Knowledge. It transports a human-confirmed Major revision into the existing
Knowledge Candidate Intake. It does not publish a Knowledge Object.

## 1. Contract shape

```json
{
  "contract_version": "major-knowledge-publication/v1",
  "producer": "MAJOR_ISSUE",
  "source": {
    "source_type": "MAJOR_EVENT",
    "source_id": "<major event source ref>",
    "business_ref": "<source-problem-itr-ref/v1.public_ref>"
  },
  "major_case_ref": "MAJOR_CASE:<business_ref>",
  "major_confirmed_revision": "<entry_id:revision_id|...>",
  "publication_revision": "MJP-<stable digest>",
  "idempotency_key": "MAJOR_ISSUE|<business_ref>|<publication_revision>",
  "knowledge_candidates": [],
  "evidence_refs": [],
  "evidence_bindings": [],
  "source_refs": [],
  "publication_status": "SUBMITTED"
}
```

`evidence_bindings` is the `common-evidence/v1.0` transport projection. It
preserves the entry/revision locator, source identity, source version, locator,
excerpt, and content hash. Major-internal `source_link_id`, `fragment_id`, and
`record_id` are never required by the consumer.

## 2. Identity and revision rules

`source.business_ref` is exactly the public
`source-problem-itr-ref/v1.public_ref` / `standard_itr`. `source.source_id` is
only the Major Event source reference; it is not the business identity.

`major_confirmed_revision` reuses the confirmed Major entry revision IDs.
`publication_revision` is a deterministic digest of the business ref, the
confirmed revision, confirmed content, and public Evidence projections. Any
confirmed revision or Evidence payload change produces a new publication
revision. The old publication remains traceable.

The suggested idempotency key is frozen as:

```text
MAJOR_ISSUE + business_ref + publication_revision
```

The same key and the same payload is `IDEMPOTENT_REPLAY`. The same key and a
different payload is `PUBLICATION_IDENTITY_CONFLICT`; neither side overwrites
the other.

## 3. Ownership boundary

Major owns `ACTIVE` eligibility, human-confirmed entries, confirmed revision,
source public ref, Evidence traceability, publication revision, and submission
status. Major does **not** set Knowledge candidate status, Knowledge Object
version, or Knowledge release version.

Unified Knowledge maps the contract into the existing business intake:

| Contract field | Knowledge mapping |
|---|---|
| `producer` | `MAJOR_ISSUE` |
| source type | `BUSINESS` / `MAJOR_ISSUE` |
| `major_case_ref` | stable `business_source_id` |
| `publication_revision` | `business_source_version` |
| `source_refs` | stable source and business refs |
| `evidence_refs` | existing Knowledge evidence intake |
| contract version | Major version retained in metadata; internal candidate remains `knowledge-candidate/v1` |

The candidate then enters the existing Evaluation, Deduplication, Conflict,
Human Review, Publish, Object Version, and Release pipeline. No review or
publish bypass is permitted. After a successful publish, the public binding
may carry `knowledge_object_ref`, `knowledge_object_version`, and the pinned
`knowledge_release_version` (`KP-STORAGE-RC1-VALIDATION-001`). Major cannot
mutate the Knowledge Object.

## 4. Eligibility and failure semantics

Submission is fail-closed unless the Major case is `ACTIVE`, all required
entries are `CONFIRMED`, a confirmed revision is available, the source public
ref is valid, every published entry has traceable Evidence, and the payload
validates.

Stable failure codes include:

```text
MAJOR_NOT_CONFIRMED
PUBLICATION_CONTRACT_INVALID
SOURCE_REF_INVALID
EVIDENCE_MISSING
PUBLICATION_IDENTITY_CONFLICT
KNOWLEDGE_CONTRACT_UNSUPPORTED
KNOWLEDGE_INTAKE_UNAVAILABLE
KNOWLEDGE_REVIEW_REQUIRED
```

Knowledge Intake failure is not a Major confirmation rollback. Confirmed Major
data remains intact and the owning Major Publication Adapter retries with the
same idempotency key.

Withdrawals express source intent/submission status only. They do not delete a
Knowledge Object; final Knowledge status is owned by Unified Knowledge.

## 5. Compatibility and rollback pin

Supported range: `V1.x_ADDITIVE_ONLY`. Breaking identity, revision, evidence,
or ownership changes require a new contract version.

Rollback pin:

```text
Major Publication Adapter: previous compatible version
Knowledge Release: KP-STORAGE-RC1-VALIDATION-001
Release binding: knowledge-release-binding/v1.0
```

The boundary is intentionally narrow:

```text
CROSS_DOMAIN_SQL=NO
KNOWLEDGE_REPOSITORY_IMPORT=NO
KNOWLEDGE_INTERNAL_MODEL_IMPORT=NO (Major producer)
MAJOR_DB_ACCESS_BY_KNOWLEDGE=NO
DATA_MIGRATION=NO
DIRECT_MERGE=NO
```

## 6. Contract gates

`P01` Contract Shape; `P02` Source Public Ref Binding; `P03` Major Confirmed
Revision Binding; `P04` Evidence Traceability; `P05` Same Revision Idempotent
Replay; `P06` Same Key Different Payload Conflict; `P07` Unconfirmed Major Fail
Closed; `P08` Knowledge Review/Publish Not Bypassed; `P09` Republish New Version
Path; `P10` Cross-Domain Boundary.
