# QS Next Stage6 — controlled original taxonomy + opt-in operator UI

**Status:** STAGE6_STANDALONE_CI_PASS / CONTAINER_MIRROR_HTTP_PASS / PRODUCT_INTEGRATION_NOT_DONE  
**2026-09-13 PATCH57 frozen source:** `6bd207e00b38e3688c817f27621bfedf052aeffb`  
**Exact historic candidate ZIP SHA256:** `8a350d4a33d26e1ab0408dcd3ebccc328b2fc83ed64a4be57f0bac7f1df36dce`  
**Stage6 stacked base:** `feature/qs-next-stage5-review-audit-20261011@1af8cfca01516e952f7ace67479c7c1ca743d751`

## Incremental features (new files only)

- `quality_knowledge/qs_next_taxonomy_gate.py` — read-only verification against original `scenario_taxonomy_version`, `scenario_lifecycle`, and `scenario_activity` tables. Requires **one** ACTIVE taxonomy for product, enabled lifecycle, enabled activity and exact lifecycle/activity relation. No original dictionary writes, no taxonomy creation, no guessing. Requires an **independent explicit product/domain approval grant**. Without hardware/mechanical approval, confirmation is denied. A CS still produces a draft candidate with missing classification.
- `quality_knowledge/qs_next_operator_app.py` — **opt-in standalone** operator HTML for three workbench entry types: CS (software/hardware/mechanical), missed-test (software), and assessment (software, only an entry). Candidate five-group review with field-level source, original evidence and explicit conflicts; human edits, conflict resolution, and audit view. This interface deliberately does NOT rewrite protected old templates or silently add links to old workbenches.
- `tests/test_qs_next_stage6_operator.py` — authorization, CSRF, original taxonomy approval, wrong activity, missing grant, source SHA no mutation, HTTP page review and confirm/reject tests.
- `.github/workflows/qs_next_stage6_operator_ci.yml` — Stage3–6 Python 3.11 tests with temp SQLite and actual FastAPI TestClient.

### UI / API protection

`create_stage6_app(original_db, new_review_db, security=TrustedReviewSecurity(...), approved_domain_scopes=..., legacy_app=...)` is **not invoked by the protected historical application**. It requires deployment-supplied trusted `authenticated_actor`, `authorize`, `verify_csrf`, and `csrf_token_for` callbacks; missing bindings fail closed. Middleware protects all `/qs-next/` and `/api/v2/qs-next/` paths, including Stage3/4 read interfaces. Every new POST requires CSRF verification; Stage5's own action permission checks apply separately.

**IMPORTANT:** These are integration extension points, not a completed production identity, RBAC or CSRF integration. The current Stage6 test uses **synthetic test-only actor/cookies/tokens**. Do **not** enable the new routes on real macOS/production until existing authentication/session/permissions and CSRF are securely bound and audited.

### Test evidence, with correct separation

**GitHub exact Stage6 source CI:** 11 Stage3 + 14 Stage4 + 15 Stage5 review + 5 aggregation + 13 Stage6 = **58/58 PASS**. GitHub workflow: [Stage6 exact source CI](https://github.com/xm286947-cell/ploblem_ai/actions/runs/38108607430).

**Independent isolated Linux container:** original PATCH57 `create_app` actually started via Uvicorn, with sandbox-only injected Stage6 UI/security/taxonomy mirror and disposable synthetic SQLite. Old endpoints 6/6 HTTP 200 and **30/30 real HTTP/flow checks PASS**. Source SQLite SHA256 unchanged before/after HTTP run, no Provider, no customer source DB. Chromium rendered 4 HTML responses fetched from the actual service via `set_content`; this is **render validation**, not interactive browser-driven navigation. Crucially, the container Stage6 overlay is **not byte-identical to the GitHub commit**; do not claim exact GitHub Stage6 code ran inside the container. GitHub CI tests the exact commit separately.

Conversation evidence ZIP: `QS_NEXT_STAGE6_CONTAINER_RUNTIME_EVIDENCE_20261011.zip` (logs, manifest, 30 HTTP checks, four screenshots). ZIP SHA256 `fd196bcf7bebd332d4fa5fe605f6b4b12b9d9b02987d3891ade0d752f3a1788f`.

## Remaining blockers and next milestone

1. **Original workbench entry-button integration** still not implemented: existing old CS/software-assessment pages untouched, missed-test entry handled by the opt-in hub. Future additive navigation or contracts require separate UED regression and return-state testing.
2. No authorized real customer data validation, Mac native UAT, verified production account/RBAC/CSRF adapter, or product security review.
3. No taxonomy *identity snapshot* persisted into confirmed audit. Stage5 currently records positive taxonomy check, but not the controlled taxonomy `version_id` / `version_no` as immutable confirmation evidence. This needs remediation before any official acceptance/publication.
4. No approved ScenarioAssets grouping execution or P04 portrait publishing. The aggregation feature is only read-only recommendations.
5. Full unrelated repo gates, including the pre-existing Hardware Case Release Prep packaging `ModuleNotFoundError: parser`, must be handled by their respective owner and not hidden by this feature CI.
6. `#610/#621/#622/#623/#626/#628` are all unmerged draft dependencies or parallel candidates; Stage6 must also remain Draft / DO NOT MERGE.

**Next Stage7**: secure real-product session adapter; auditable taxonomy version snapshot; safely add entry navigation to the three original business contexts; only then consider the approved scenario-asset/P04 integration in a separate branch. Mature PATCH57 source and macOS manual UAT remain frozen and parallel.
