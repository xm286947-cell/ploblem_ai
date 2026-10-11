# QS Next — Stage 3: Isolated Live-HTTP Material Gateway (2026-10-11)

**Status:** STANDALONE_BRANCH_CI / CONTAINER_PROTOTYPE_PASS / INTEGRATION_AND_REAL_DATA_PENDING

## Authoritative boundaries

- Legacy reference: 2026-09-13 frozen product source `6bd207e00b38e3688c817f27621bfedf052aeffb`.
- Drive-backed original-source candidate: `LEGACY_QUALITY_PATCH57_20260913_SOURCE_CANDIDATE.zip`, verified SHA256 `8a350d4a33d26e1ab0408dcd3ebccc328b2fc83ed64a4be57f0bac7f1df36dce`.
- Development starts from `main@de8bbd6de1513b9bdceb9a0336b163d5595244cb`; it does **not** alter or merge #610 / #621 / #622.
- This PR adds a **read-only, opt-in** APIRouter using the historical material table contract. It is not mounted by main and is not a product release.

## Customer-defined domain policy

| Entry | Existing material type | Allowed domains | Formal source |
| --- | --- | --- | --- |
| 彻底解决 | ITR_CS | SOFTWARE/HARDWARE/MECHANICAL (including mixed) | THOROUGH_SOLUTION_ORDER |
| 漏测分析 | ESCAPE_ANALYSIS | SOFTWARE only | MISSED_TEST_ANALYSIS |
| 软件考核 | SOFTWARE_OPERATION | SOFTWARE only | Entry/context only; resolve CS and/or missed-test |

ITR is an associated problem identity, not a production source. A missed-test
report must **not** be used as a hardware-only or mechanical-only CS formal
source, even when the legacy association table relates them to one problem.

## New gateway behavior

`quality_knowledge/qs_next_gateway.py` exposes **only if explicitly mounted**:

`GET /api/v2/qs-next/entry/v1/{workbench}/{material_id}`

This function creates a **formal-source read plan**, not a scenario candidate.
It queries the existing `source_material`, `data_group` and
`issue_material_link` tables using `sqlite3` `mode=ro` +
`PRAGMA query_only=ON`. There is no migration, no SQL mutation, no
Provider call, no evidence fabrication, and no scenario publication.

The original `material_id`, `material_type`, `source_hash`,
`version_no`, original business key and canonical ITR context remain
separate. Associated formal records must be joined by the existing verified
`LINKED` / `MANUAL_LINKED` relationship; same-ITR textual matching alone
is insufficient. Stale source revisions are rejected.

For a software assessment without a confirmed formal source, the gateway
returns `FORMAL_SOURCE_REQUIRED`; for CS without an explicit controlled
domain, `DOMAIN_REVIEW_REQUIRED`. `FULL` source coverage means both
source *types* are available, **not** full evidence/field coverage.

## Own-container runtime validation

Using an isolated extracted copy of the original source ZIP, **not the
original protected macOS runtime**:

1. Real process: `python main.py knowledge-web --db <isolated-temp.db> --host 127.0.0.1 --port 18080`; Uvicorn startup completed.
2. GET `/issues`, `/materials/cs`, `/materials/software-operations`,
   `/quality-scenarios`, `/quality-scenario-assets`, and `/openapi.json`:
   **6/6 HTTP 200**.
3. Five actual HTTP HTML/CSS responses were rendered via Chromium
   `page.set_content` and captured as screenshots. Browser-initiated
   localhost navigation was blocked by administrator network policy, so
   this is **HTML/CSS render verification**, **not** full Chromium navigation E2E.
4. A sandbox-only ASGI wrapper mounted `create_router(<isolated-temp.db>)`
   onto the unmodified original `create_app` instance on port 18081.
5. **13/13 live HTTP scenarios PASS** using synthetic rows inserted only
   into a disposable SQLite file; plus explicit negative verification that a
   hardware-only CS does not consume a linked software missed-test as coverage.
6. The temporary database file's SHA256 was identical before and after a
   repeated set of gateway requests.

The container prototype and GitHub-committed implementation are separately
validated; the container cannot access GitHub DNS, so **do not claim it ran a
fresh checkout of this Git commit**. GitHub CI separately compiles and
exercises the exact committed module against a hermetic PATCH57-shaped
SQLite schema and a FastAPI TestClient.

**Runtime evidence bundle** (screenshots, startup logs, actual HTTP results,
sandbox gateway harness, manifest): `QS_NEXT_STAGE3_CONTAINER_RUNTIME_EVIDENCE_20261011.zip`
in the ChatGPT delivery. No customer data or original DB is included.

## Integration gates NOT PASSED

- No actual user-authorized real business DB read.
- No real source field/evidence extraction from raw material.
- No five-dimensional scenario candidate generation or human confirmation.
- No new entry buttons in the historical workbench UIs.
- No actual browser navigation E2E due container policy.
- No authentication/authorization/CSRF integration gate for the new route;
  **do not mount the router on production until existing security controls
  are explicitly inherited and security reviewed**.
- No UAT on user's native macOS workbench, nor full old+new regression.
- No approval to merge #610/#621/#622 or this branch.

## Next separate milestone

Stage 4: an authorized, read-only `MaterialReader` provider returning
source-field locators and content-hash/revision identity, then deterministic
mapping to the mature five-dimensional software/hardware/mechanical model.
Only then propose a separate candidate-production and human-review PR.

The baseline stays frozen. This change is draft only.
