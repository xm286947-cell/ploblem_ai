# QS Next Stage 5 — Human review, audit trail and strict grouping suggestions

**Status:** STANDALONE_DRAFT / NOT_PRODUCT_CONNECTED / REAL_DATA_GATE_PENDING
**Historical frozen source:** `6bd207e00b38e3688c817f27621bfedf052aeffb` (PATCH57)
**Stack base:** `feature/qs-next-stage4-five-dimension-candidate-20261011@cdb0756d59bda71bd4ef02f6d69c99963e517ffe`

## Delivered incremental capabilities

### Isolated review store

`quality_knowledge/qs_next_review.py` adds a review-only opt-in FastAPI router,
not wired to original `create_app`, original pages, original SQLite, or P04.

- Original read-only `source_db` must differ from new `review_db`.
- Start a deterministic single-issue candidate with idempotent retry and
  unaltered source/evidence snapshot.
- Apply manual corrections in a separate review DB; store the human actor,
  original source facts, correction, reason, revision and provenance.
- Project corrected effective values back into the five-dimensional review view
  while preserving all original field evidence.
- Optimistic revision checks prevent a stale reviewer overwriting a newer edit.
- Review events are inserted into a separate audit table; the application has
  no delete/replace audit operation. This is an application-level audit log,
  **not yet a cryptographically tamper-proof, immutable external journal**.
- Controlled statuses: `IN_REVIEW`, `CONFIRMED`, `REJECTED`. Final states
  cannot be edited. `CONFIRMED` is a human review outcome and does **not**
  imply published QualityScenarioV1 or historical ScenarioAssets assets.
- Domain-specific human edits cannot cross SOFTWARE/HARDWARE/MECHANICAL.
- A `CONFIRM` requires every controlled mandatory field, explicit conflict
  resolution, and a trusted, positive lifecycle/business-activity taxonomy
  checker; missing checker means **fail closed**.
- An HTTP caller cannot impersonate reviewer through payload because actor
  identity is injected through a **trusted actor resolver**; authorization
  checker is mandatory and absent by default. CSRF/permission integration for
  actual UI is **NOT DONE**: do not mount in product.

### Conservative multi-problem suggestions

`quality_knowledge/qs_next_aggregation.py` reads **only confirmed** review
records and emits **SUGGESTION_ONLY**; it never calls
`ScenarioAssets.group()` or creates an asset.

Pair candidates must come from **different original problems**, same original
product code, exactly one matching domain, same lifecycle code and business
activity, and same domain-specific failure mode. Conflicting quality attributes
are excluded. Source drift is skipped. Missing product/activity/identity yields
no recommendation. The output preserves the source evidence and human edit
record and always says `asset_created=false`, `auto_grouped=false`.

**This is strict deterministic matching, NOT semantic AI similarity.** Future
semantic candidate generation must be independently validated with negative
examples and human approval.

### Stage5 API (opt-in only, not product mounted)

- `POST /api/v2/qs-next/review/v1/start/{workbench}/{material_id}`
- `GET /api/v2/qs-next/review/v1/{review_id}`
- `GET /api/v2/qs-next/review/v1/{review_id}/audit`
- `POST /api/v2/qs-next/review/v1/{review_id}/revise`
- `POST /api/v2/qs-next/review/v1/{review_id}/decide`
- `GET /api/v2/qs-next/aggregation/v1/suggestions`

## Boundary protections

- Mature baseline stays frozen. All new files only; earlier modules, database
  tables, existing quality workbench pages and P04 are untouched.
- Formal sources remain `ITR_CS` (THOROUGH_SOLUTION_ORDER) and
  `ESCAPE_ANALYSIS` (MISSED_TEST_ANALYSIS). Software KPI is an initiation
  entry/context only; ITR remains relationship context, never production source.
- Thorough solution covers SOFTWARE/HARDWARE/MECHANICAL; missed-test and
  software assessment are SOFTWARE only.
- No Provider used, no backfill, no data migration, no asset publication.
- Review and proposed grouping are separate from original confirmed published
  scenario assets; product integration requires a formally reviewed adapter.

## Verification levels

**Exact GitHub source CI** tests stages 3, 4, 5 review and aggregation on
temporary SQLite/FastAPI TestClient. Tests cover denied access without trusted
auth binding, both human confirmed and rejected terminal states, revision
conflicts, source snapshot drift, conflict resolution, controlled taxonomy gate,
duplicate problems, cross-product/activity non-aggregation, and no source data
mutation.

**Isolated own Linux container runtime** launches the original PATCH57
`create_app` with a **sandbox review HTTP mirror** beside Stage4 sources,
using a disposable SQLite and a separate review DB. Synthetic real HTTP
checks cover old pages, preview → start → revise → audit → reject, optimistic
collision, missing evidence, and denied unverified confirmation. This is an
actual Uvicorn process, but the **review mirror is not byte-for-byte the GitHub
Stage5 implementation**. Exact submitted implementation is validated by CI,
not by local container Git checkout (container has no GitHub DNS).
See conversation artifact `QS_NEXT_STAGE5_CONTAINER_RUNTIME_EVIDENCE_20261011.zip`.

## Unpassed integration/release gates

- No actual macOS original database, no real customer source case or Provider.
- No original PATCH57 controlled taxonomy service bound to the checker yet.
- No actual human identity/role/CSRF binding, workbench button or UI workflow.
- No secure deployment hardening / audit immutability guarantee.
- No original ScenarioAssets human-approved merge/publish implementation.
- No formal end-to-end product test or native UAT; no PR merge approval.
- Upstream staged PR #623/#626, and independent #610/#621/#622 remain
  open/unmerged, and all applicable regression gates still required.

**Follow-up:** controlled real source reader + taxonomy binding, genuine
role-gated review UI, approved ScenarioAssets adapter, then native macOS E2E
and formal product acceptance. No premature release status.
