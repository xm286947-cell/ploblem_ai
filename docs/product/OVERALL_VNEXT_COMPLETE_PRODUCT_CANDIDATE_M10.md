# Overall VNext Complete Product Candidate — M10

TASK=OVERALL-VNEXT-SINGLE-DEV-TAKEOVER-001
MILESTONE=M10_COMPLETE_PRODUCT_CANDIDATE
CANDIDATE_ID=OVERALL-VNEXT-CANDIDATE-2026-09-27
CANDIDATE_DATE=2026-09-27
SOURCE_MAIN_SHA=dffa846cd30f8dcaf92658f40e29c3cc713be5d6
INTEGRATION_BRANCH=integration/overall-vnext
INTEGRATION_HEAD_BEFORE_M10_MANIFEST=5af79f8cc6f2f5ea520caa5cb0e1b44ca1956e42
ARCH_REVIEW=PASS_WITH_CONDITIONS
ARCH_BLOCKER_COUNT=0
MILESTONE_REGRESSION=104 passed
DEPLOYMENT=NOT_EXECUTED

## Candidate scope

- One existing `create_p0_app` FastAPI host and Overall Shell.
- The `/` default continues to redirect to `/p0/issues`; the Overall Shell provides a direct entry to the existing issue workspace.
- Major / Repeat Risk, Quality Scenario, Hardware Case, and Storage remain existing workspaces with their current domain ownership and route contracts.
- Common Evidence remains a reference and navigation surface. No second web application, port, cross-domain database, or business schema was introduced.
- The implementation delta against `SOURCE_MAIN_SHA` is limited to this implementation plan, an explicit root `httpx` dependency, the problem-workspace return link, and its integration regression test. The `httpx` declaration closes an existing direct import requirement for the host test path.

## Release manifest

| Field | Value |
| --- | --- |
| Runtime entry | Existing single FastAPI host created by `create_p0_app` |
| Overall entry | `/p0/overall` |
| Default workbench | `/p0/issues` |
| Integration branch | `integration/overall-vnext` |
| Candidate source main | `dffa846cd30f8dcaf92658f40e29c3cc713be5d6` |
| Candidate branch head | `5af79f8cc6f2f5ea520caa5cb0e1b44ca1956e42` |
| Root test dependency | `httpx>=0.27,<1` alongside existing `httpx2` |
| Domain migrations | None added by this candidate |
| Second host / port | None |
| Production promotion | Not performed; canonical PR review is the promotion gate |

## Verification evidence

M1–M8 milestone checks are recorded in `VNEXT_IMPLEMENTATION_PLAN_V0.1.md`. The M9 cross-milestone regression passed 104 tests across 20 targeted modules, including Overall Shell, issue routing, Major / Repeat Risk, Hardware Case, Quality Scenario and insights, Storage, Evidence / Source, settings, forward risk, product reporting, and published knowledge contracts.

Reproduce the M9 regression from repository root with the exact command in the M9 section of `VNEXT_IMPLEMENTATION_PLAN_V0.1.md`.

## Rollback path

1. Before promotion, close or revise the canonical PR and keep the current `main` deployment pinned to `SOURCE_MAIN_SHA`.
2. If the canonical PR is merged and a rollback is needed, create a revert PR for that canonical merge and promote the prior `main` release at `SOURCE_MAIN_SHA` through the normal release process.
3. This candidate adds no database or data migration, so rollback does not require cross-domain data repair.
4. Preserve the candidate branch and its regression evidence for diagnosis; do not rewrite the integration branch history.

## TSE handoff

The branch is a runnable candidate for Product Test Center / TSE review. TSE can use the M9 command and the milestone evidence above as the initial regression set. Legacy routes not consumed by this candidate remain subject to feature-local targeted verification; they do not block this candidate or reopen the superseded full audit.

Canonical PR: to be created from `integration/overall-vnext` to `main` after candidate manifest commit.
