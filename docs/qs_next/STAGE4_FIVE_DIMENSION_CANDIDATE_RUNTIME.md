# QS Next Stage4 — Five-dimensional candidate preview and runtime gate

**Status:** INDEPENDENT_STAGED_DRAFT; SOURCE_FACT_PREVIEW_PASS; REAL_BUSINESS_UAT_NOT_RUN.

## Source and branch identity

- Historical frozen PATCH57 source Git SHA: `6bd207e00b38e3688c817f27621bfedf052aeffb`.
- The exact legacy SOURCE_CANDIDATE.zip used in the own-container test had SHA256 `8a350d4a33d26e1ab0408dcd3ebccc328b2fc83ed64a4be57f0bac7f1df36dce`.
- Stage4 branch: `feature/qs-next-stage4-five-dimension-candidate-20261011` from frozen Stage3 PR #623 head `d0da42a5a0db6d40d802e6b3fe9536e1f4be43db`.
- This is a **stacked, dependent draft**, not a merge into `main`.
- Stage4 only adds source/evidence mapping preview, tests, CI and documentation; no previous module or legacy implementation is rewritten.

## What is actually implemented

Additional opt-in GET endpoint:
`/api/v2/qs-next/candidate/v1/{workbench}/{material_id}`

`THOROUGH_SOLUTION` (CS) accepts software, hardware, mechanical and explicitly mixed CS domains. `MISSED_TEST` and `SOFTWARE_ASSESSMENT` are software-only. Workbench entry is separate from **formal production source**: original `ITR_CS` (THOROUGH_SOLUTION_ORDER) and `ESCAPE_ANALYSIS` (MISSED_TEST_ANALYSIS). Assessment KPI is never a formal production fact; ITR is trace context only.

The route uses Stage3's original SQLite `mode=ro` / `query_only=ON` material gateway, preserves every material ID/version/hash and supports exactly one candidate preview per entry request. Its stable `preview_id` binds to source revisions and hashes.

Preview response contains **five evidence-preserving presentation groups** of mature individual business fields:

1. `usage_context` — product/customer/user/lifecycle/business-activity context
2. `failure_behavior` — perception/symptom/failure mode/mechanism with SOFTWARE/HARDWARE/MECHANICAL fields **kept separately**
3. `trigger_and_conditions` — preconditions, triggers, environment, workload/duration, system scale, boundary
4. `customer_impact` — affected object, business consequences, recovery
5. `quality_and_validation` — experience requirement, quality attribute, test direction, measurement, confirmed source escape reason

**Important:** the five groups are a candidate **presentation view over original PATCH57 field semantics**. They do NOT certify a complete restored legacy business model or product taxonomy. Unavailable historical fields stay absent; no invented defaults. Lifecycle and business activity are marked for controlled taxonomy review. No machine inference or Provider is called.

Every mapped fact has source material ID, revision, 64-char source hash, original raw Excel/material field, stable raw-field locator, literal source value, and provenance kind. Two different formal sources disagreeing on a field are both preserved and flagged for human review; they are **not auto-overwritten**. Software KPI context never enters formal candidate facts.

Candidate status is `PENDING_HUMAN_REVIEW`, `publish_ready=false`; response `persisted=false`, `human_confirmed=false`. No original business table, scenario asset or candidate store is written. A blocked entry returns a controlled `BLOCKED` result with no candidate.

## Real startup and test scope

Two separate validation systems have been exercised:

**A. GitHub CI on exact committed implementation** (Python 3.11):
- previous Stage3 gateway: **11 tests PASS**
- Stage4 candidate field/HTTP evidence: **14 tests PASS**
- CI workflow: `QS Next Stage4 Five Dimension Preview`

**B. Own isolated Linux container** (Python 3.13):
- Re-extracted/copy-isolated original PATCH57 Web application with unmodified original routes.
- Actual Uvicorn process and FastAPI ASGI using opt-in Stage3/Stage4 sandbox mirrors on localhost port 18082.
- Old `/issues`, `/materials/cs`, `/materials/software-operations`, `/quality-scenarios`, `/quality-scenario-assets`, `/openapi.json`: **6/6 actual HTTP 200**.
- New `/api/v2/qs-next/candidate/v1/` live HTTP: **10/10 cases PASS** (3 CS problem domains, mixed domain, missed-test, software assessment, no formal source, unclassified domain, stale source, workbench mismatch).
- Extra field checks: 5 distinct groups, software/hardware/mechanical failure-mode separation, raw-field evidence locator, assessment KPI exclusion, two-source field conflict for human review, deterministic preview ID, empty-field blockers.
- New gateway HTTP read test left isolated SQLite exact bytes unchanged before/after: SHA256 `f4816dced7187a1f158895eeb6eb7b92b22ff1740629dfe4a9e4b1121f3cd861`.
- Evidence artifact: `QS_NEXT_STAGE4_CONTAINER_RUNTIME_EVIDENCE_20261011.zip` in this conversation.
- Container code is a **mirrored sandbox prototype**, not a checked-out GitHub commit (container GitHub DNS unavailable); do not conflate with CI on the exact Git commit.

## Not done: mandatory next gates

- No authorized real customer database or source document field validation.
- No Provider/AI invocation, no actual controlled original taxonomy classification.
- No persisted candidate, role-based human confirmation/rejection, or audit trail.
- No original workbench buttons / real user navigation / Product UED integration.
- **No authentication/authorization/CSRF review on opt-in new endpoint** — DO NOT mount this route in production until proper security integration is verified.
- No full original+new cross-workbench native macOS UAT, nor permission to merge PR #610, #621, #622, #623 or Stage4.

Stage5 must resolve real data/provenance and five-dimensional business review using the original scenario repository Contract **before** implementing an additive human review workflow, product integration and any merge decisions. Keep the September 13 source and macOS manually tested baseline untouched.
