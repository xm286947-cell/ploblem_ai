# Hardware R1 Wave 3A — Consumption Foundation

Issue: [#394](https://github.com/xm286947-cell/ploblem_ai/issues/394)

Contract: `hardware-knowledge-consumption/v1`

## Ownership and boundary

```text
Formal Unified Knowledge
        │ HardwareCaseKnowledgeAdapter public contract only
        ▼
rebuildable SQLite projection (Tier C)
        ▼
read-only, explainable consumption API
```

Unified Knowledge is the only Formal Knowledge source of truth. The SQLite
projection lives at
`<PERSISTENT_DATA_ROOT>/rebuildable/hardware_knowledge_consumption.db` and is
disposable/rebuildable. It does not store review or publish mutations. Hardware
code does not open the Unified Knowledge database or repository.

The existing `hardware-public-consumer/v1` router and schema remain independent
and unchanged. This foundation adds a separate read-only prefix:
`/api/public/hardware-knowledge/v1`.

## Eligibility and projection

Only local `hardware_asset_promotion` records whose candidate and ledger agree
on `promotion_status=VERIFIED`, and which contain both `knowledge_id` and
`public_ref`, are eligible. Each eligible Formal object is resolved through
`HardwareCaseKnowledgeAdapter.get_object()` and checked for its Hardware Case
domain/type, knowledge ID, revision, public reference, business-case identity,
source identity, and evidence identity before projection.

Field mapping copies explicit values from the frozen
`hardware-case-knowledge-object/v1` content. Missing fields remain `null` or
empty arrays. Device references use only explicit device/context values;
category, manufacturer, material number, MPN, and device-level evidence remain
null/empty unless explicitly present. No supplier, MPN, topology, pin, placement,
or wiring inference is performed.

`project_verified(asset_candidate_id)` refreshes one eligible record.
`rebuild_all_verified()` resolves the full eligible set, writes a staged SQLite
database, validates it, then atomically replaces the projection file. A failed
resolution or write leaves the prior readable projection untouched. The
projection status distinguishes `MISSING`, `CORRUPT`, `INCOMPATIBLE`, and
`READY`; a full rebuild can recover missing, corrupt, or incompatible projection
state without changing durable assets.

An operations-only helper is available for status and explicit rebuilds:

```text
python scripts/hardware_knowledge_consumption_projection.py status --data-root <PERSISTENT_DATA_ROOT>
python scripts/hardware_knowledge_consumption_projection.py rebuild --data-root <PERSISTENT_DATA_ROOT> --knowledge-base-url <PUBLIC_API_URL> --knowledge-release-version <RELEASE_VERSION>
python scripts/hardware_knowledge_consumption_projection.py project --data-root <PERSISTENT_DATA_ROOT> --asset-candidate-id <ASSET_CANDIDATE_ID> --knowledge-base-url <PUBLIC_API_URL> --knowledge-release-version <RELEASE_VERSION>
```

The builder requires the existing Asset DB and the configured public Knowledge
API. It performs reads through that contract and writes only the rebuildable
projection.

## Read contract and retrieval V0

- `GET /search`: `text` plus exact structured filters for `knowledge_id`,
  `business_case_id`, `source_domain`, `source_object_type`, `interface`,
  `signal`, and `device`.
- `GET /objects/{knowledge_id}`: fetch one projected record.
- No write, review, publish, or projection-rebuild route is exposed publicly.

Text is normalized using Unicode NFKC, whitespace trim/collapse, and casefold;
matching is literal substring only. Each matching field contributes its frozen
weight once, with a reason containing the matched field, `SUBSTRING`, normalized
query text, and weight. Results sort by score descending then `knowledge_id`
ascending. Empty text with structured filters is allowed and returns score `0`
with no match reasons.

| Field | Weight | Field | Weight |
| --- | ---: | --- | ---: |
| title | 100 | symptom | 90 |
| root_cause | 90 | failure_mechanism | 85 |
| engineering_rule | 80 | design_constraint | 80 |
| diagnostic_clue | 75 | verification_method | 70 |
| actions | 70 | interface | 65 |
| signal | 65 | key_parameters | 60 |
| device_refs | 60 | occurrence_condition | 55 |
| analysis_process | 50 | verification_result | 50 |
| conclusion | 50 | applicability | 45 |

`failure_mode` remains available in projected records but has no retrieval
weight in frozen V0; no new weight is inferred.

## Out of scope

No UI or Wave 3B business scenarios; no OpenSearch, vector search, embeddings,
RAG, multi-agent, new runtime/knowledge stack, tree prerequisite, formal
knowledge write, Agent/prompt/model/validator change, or D1/D2 durability change.
