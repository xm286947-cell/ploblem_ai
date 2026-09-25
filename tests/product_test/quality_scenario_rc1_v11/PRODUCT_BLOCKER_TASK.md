# PRODUCT BLOCKER — QUALITY SCENARIO V1.1 Browser Golden Handoff

TASK=QUALITY-SCENARIO-V1.1-P0-BROWSER-HANDOFF-001  
PRIORITY=P0  
SOURCE=Product Test Center V1.1 Audit  
OWNER=Quality Scenario Product Manager + Development  
TEST_OWNER=Quality Scenario Test Team

## Single goal

Make the frozen product Golden Path executable from a real user/browser entry without test-side API/DB injection:

Source Problem / ITR Web
→ Reverse Quality AI
→ ReverseQualityResult
→ **formal user handoff**
→ QualityScenario V1 Candidate
→ P01
→ Review/Edit
→ dual confirmation
→ Publish
→ P02
→ P03
→ Evidence / Source reverse trace.

## Current factual gap

The existing Reverse Quality page has AI analysis and human review controls. P01 has Candidate workflow controls. The product currently lacks a user-visible/actionable handoff between them. Engineering tests bridge the domains by calling the V1 Candidate API directly.

Under Product Test Center V1.1, the API bridge cannot be used as Golden E2E proof.

## Product constraints

- Do not add a second workflow or approval system.
- Do not change the frozen main states: CANDIDATE → CONFIRMED → PUBLISHED; CANDIDATE → REJECTED.
- Reuse the formal ReverseQualityResult → Candidate V1 contract.
- Preserve Source Problem, Evidence, trigger_source and trigger_reason.
- Do not auto-confirm or auto-publish.
- Missing Information / blockers remain visible and blocking.
- No test-only button or hard-coded fixture behavior.

## Acceptance

From a clean product package using only Browser interactions plus Provider-only Mock:
1. user opens an approved source problem;
2. triggers AI analysis;
3. sees Reverse Quality result/evidence;
4. uses the formal product action to create/continue to Candidate;
5. P01 shows the same source/evidence/trigger context;
6. the created entity has a stable scenario_id;
7. no direct DB write by test;
8. no external API call by test to manufacture Candidate;
9. HIGH_PERCEPTION and RND_VALUE use the same handoff/state machine;
10. candidate can then complete P01→Publish→P02→P03→Evidence.

## Delivery

Because this changes product code/capability:
NEW_PRODUCT_RC_REQUIRED=YES

Return:
SOURCE_COMMIT
BUILD_COMMIT
NEW_PACKAGE
NEW_SHA256
PACKAGE_SELF_CHECK
READY_FOR_V1_1_FORMAL_RETEST=YES

Development must not modify test Expected, Fixture, Browser Flow or Coverage Matrix.
