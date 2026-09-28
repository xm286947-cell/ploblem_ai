# Overall VNext Integration Candidate Status

TASK=OVERALL-VNEXT-SINGLE-DEV-TAKEOVER-001
STATUS=INTEGRATION_VALIDATION_IN_PROGRESS
M10_COMPLETE_PRODUCT_CANDIDATE=NOT_READY
INTEGRATION_BRANCH=integration/overall-vnext
INTEGRATION_HEAD_BEFORE_STATUS_UPDATE=b4ba408df689395f8a9b114194f8eb2aa18793e8
SOURCE_MAIN_SHA=11a71c7d0155772044752012ffe3d4cc3546e32a
ARCH_REVIEW=PASS_WITH_CONDITIONS
ARCH_BLOCKER_COUNT=0
DEPLOYMENT=NOT_EXECUTED
PRODUCT_TEST_GATE=NOT_CLAIMED

## Current implementation evidence

- The existing `create_p0_app` FastAPI host and Overall Shell remain the single entry point.
- The Overall areas and existing Major / Repeat Risk, Quality Scenario, Hardware Case, Storage and Legacy pages are reachable through the integrated host.
- Synthetic OpenAI Mock validation: 78 passed, recorded against its own source commit.
- Browser navigation check: 9 passed on the synthetic demo candidate. Mobile evidence covers the Overall home at 390×844 only.
- Candidate ZIP `OVERALL_VNEXT_FAST_MVP_CANDIDATE_8648fe3.zip` belongs to demo commit `8648fe3bd8f4ad18202974a917e3695e89287926`; it is a development demo artifact and is not the canonical integration DUT.
- Fresh targeted Legacy + Overall integration regression: 16 passed, including the synthetic Current Issue Golden Path, on the integration candidate containing test commit `09d527a06c8a4d2f4f7f77b643bf8894d9fddd23`.
- Fresh Cases/Knowledge cross-suite integration regression: 45 passed on `21fc3105f016819994b1a9dc4af132bf7e6c4742`; it covers case-to-candidate, Human Gate review/publish, release/query consumption, consumer contracts, and Overall/Storage binding. The synthetic demo launcher `--check` also passed on a clean data directory. This is cross-suite evidence, not one browser-driven end-to-end test.
- Earlier M1–M9 result of 104 passed is historical evidence tied to the source SHAs recorded in `VNEXT_IMPLEMENTATION_PLAN_V0.1.md`. It is not a fresh regression result for this integrated commit.

## Remaining M10 acceptance work

Complete the business journeys from the frozen product scenarios and record the persistent outcomes:

1. Quality scenario: insight view → P03 detail → Source/Evidence → return, including refresh, Back/Forward, URL state versus stale session state. This remains untested pending exact DUT/runtime binding and explicit S11 authorization.
2. Mobile: validate the frozen high-fidelity pages and key workflows on a normal CJK-capable runtime.
3. Run targeted integration regression against the final canonical candidate and bind the exact source commit, runtime package and evidence.

Mocks may replace only the AI Provider boundary. The application workflows, persistence, review states and consumption should exercise the real implementation using synthetic data.

## Promotion gate

M10 is ready for canonical promotion only after the scoped business journeys pass, the final candidate is reproducible from a remote-resolvable integration commit, regression evidence and rollback path are attached, and Product Test Center / TSE accepts the package. Until then this branch is integration validation in progress.

Canonical PR: https://github.com/xm286947-cell/ploblem_ai/pull/212 (open, targets main).
