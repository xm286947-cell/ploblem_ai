# R2_IMPLEMENTATION_PLAN_V1.2

TASK=OVERALL-R2-CAPABILITY-SCOPE-AND-IMPLEMENTATION-PLAN-001  
STATUS=READY_FOR_WAVE_EXECUTION  
PRODUCT_INPUT=OVERALL_R2_PRODUCT_REQUIREMENTS_AND_APPLICATION_SCENARIOS_V1.2_20260929  
SCOPE_MATRIX=docs/product/R2_CAPABILITY_SCOPE_MATRIX_V1.2.md  
R2_BRANCH=integration/overall-r2  
GLOBAL_EXISTING_CAPABILITY_AUDIT_REOPEN=NO  
TARGETED_VERIFICATION_ONLY=YES  

## 0. Execution principles

1. One formal branch: `integration/overall-r2`.
2. Current R2 is the implementation baseline; `integration/overall-vnext` is reference-only.
3. No whole vnext merge. Every reused demo change requires Targeted Diff Review.
4. Existing business state machines/data ownership remain in their domains.
5. Overall may aggregate, navigate and enforce public contracts; it must not create parallel masters or private provider/runtime stacks.
6. Unknown route/binding is resolved by Targeted Verification, not a repository-wide Existing Capability Audit.
7. Every wave ends with focused regression + previous-wave regression + Windows clean-start check where the change affects assembly/runtime/deployment.

## Wave 1 — Canonical Problem / Current Problem

### Objective
Freeze one Canonical Problem Identity and complete the long-term Common Problem View + four-business-workbench model.

### Scope
- Canonical Problem Identity contract.
- Common Problem View as shared list/detail/navigation surface, not a fifth workbench.
- ITR / field recovery.
- Resolution / thorough-solve workbench.
- Software assessment.
- Missed-test analysis.
- Cross-workbench relation.
- Deep Link / Return Context.
- NO_RELATION / ambiguous relation fail-closed.
- Real Existing capability only; no `/analysis` substitution.

### First task
`R2-W1-CANONICAL-PROBLEM-CONTRACT-AND-FOUR-WORKBENCH-CLOSURE-001`

### Required targeted verification
- canonical business identity keys in Existing Problem / quality_issue.
- source-owned action/state contracts for Resolution.
- authoritative software-assessment state/permission owner.
- exact relation keys and source refs.
- current Deep Link/Return bindings.

### Domain minimal fix
DF-01 Existing Problem.

### Architect decision
ARCH-01 Canonical Problem Identity / Cross-workbench Relation ownership and versioning.

### UED delta
UED-01 Common Problem View / Current Problem landing.

### Scenario closure
SCN-R2-01 / 02 / 03 / 04 / 05.

### DoD
CANONICAL_PROBLEM_IDENTITY=PASS  
FOUR_WORKBENCH_MODEL=PASS  
COMMON_PROBLEM_VIEW_NOT_FIFTH=YES  
CROSS_WORKBENCH_RELATION=PASS  
DEEP_LINK_RETURN=PASS  
NO_RELATION_FAIL_CLOSED=PASS  
REAL_EXISTING_WORKBENCH=PASS  
SECOND_PROBLEM_MASTER=NO  

## Wave 2 — Major Case Production

### Objective
Make Excel a governed first-class Historical/Major Case production entry and prove convergence into the existing case production/repeat chain.

### Scope
- Official Excel template and template_version.
- Upload / Preview / Mapping / Confirm / Import.
- mapping_version.
- import batch_id / run_id / actor / timestamp / result detail.
- atomic/fail-closed import.
- Source Fact creation.
- Source Fact → AI Analysis → Human Review → Confirmed → Publish → Historical Case.
- Repeat Risk consumption.
- Single-event / Excel-batch convergence at Source Fact.

### Domain minimal fix
DF-02 Major Case.

### UED delta
UED-02 Major Case Excel maintenance states/version/error details.

### Scenario closure
SCN-R2-06 / 07 / 08, plus Major portion of SCN-R2-04.

### DoD
OFFICIAL_TEMPLATE=PASS  
TEMPLATE_VERSION_GOVERNANCE=PASS  
MAPPING_VERSION_GOVERNANCE=PASS  
IMPORT_BATCH_TRACE=PASS  
PARTIAL_VALID_CASE=NO  
SOURCE_FACT_CREATED=PASS  
AI_REVIEW_PUBLISH_CONTINUATION=PASS  
SINGLE_BATCH_CONVERGENCE=PASS  
REPEAT_CONSUMABLE=PASS  

## Wave 3 — Hardware Productization

### Objective
Turn current Hardware integration into a formal server-authorized CONSUMER/MAINTAINER product model without changing Hardware business ownership.

### Scope
- CONSUMER / MAINTAINER role model.
- Navigation visibility.
- Route authorization.
- API authorization.
- Reject query-param/local-storage/front-end role escalation.
- P01-P07 compatibility.
- Knowledge Intake / Case Confirm / Base Data maintenance entries.
- Maintenance audit: Confirm / Publish / Tree Apply / DEPRECATE / Mapping revision.
- P07 tree import/version/history atomic semantics.

### Domain minimal fix
DF-03 Hardware Case.

### Architect decision
ARCH-02 trusted authorization context + audit actor contract.

### UED
No mandatory redesign; use current pages. Only focused delta if server-side auth state needs a visible forbidden/role-state treatment.

### Scenario closure
SCN-R2-09 / 10 / 11 / 12 / 13.

### DoD
ROLE_MODEL=PASS  
NAVIGATION_VISIBILITY=PASS  
ROUTE_AUTHORIZATION=PASS  
API_AUTHORIZATION=PASS  
NO_QUERY_PARAM_ESCALATION=PASS  
MAINTENANCE_AUDIT=PASS  
CONSUMER_REGRESSION=PASS  
MAINTAINER_E2E=PASS  

## Wave 4 — Storage Business Flow / Knowledge / Operability

### Objective
Rebase Storage around ProjectDeviceContext and one business flow, while keeping Unified Knowledge authoritative for production/publish.

### Scope
- ProjectDeviceContext identity/projection.
- Project / Device Matrix landing.
- Device Context → Fact → Lifetime / Observation / Diagnosis → Software/Test Impact → Engineering Decision.
- One Storage Knowledge maintenance flow.
- One source intake.
- Source → Extraction → Candidate → Evidence/Evaluation → Review → Publish → Formal Release → Storage Consume.
- Cross-domain status / failure / next-action projection.
- Storage AI Operability status card.
- Deep link to Overall Agent Config / Runtime Diagnostics.
- No Storage-private Provider config center.

### Domain minimal fixes
DF-04 Storage.  
DF-05 Unified Knowledge.  
Storage adapter portion of DF-06 only where required for operability projection.

### Architect decisions
ARCH-03 ProjectDeviceContext identity/ref.  
ARCH-04 Storage ↔ Unified Knowledge projection boundary.

### UED deltas
UED-03 Storage Matrix / Device Context.  
UED-04 Storage Knowledge single flow + Operability card.

### Scenario closure
SCN-R2-14 / 15 / 16.

### DoD
PROJECT_DEVICE_CONTEXT=PASS  
SECOND_DEVICE_MASTER=NO  
STORAGE_MATRIX=PASS  
ONE_USER_KNOWLEDGE_FLOW=PASS  
ONE_SOURCE_INTAKE=PASS  
DUPLICATE_KNOWLEDGE_PRODUCTION=NO  
FORMAL_RELEASE_TRACEABLE=PASS  
STORAGE_CONSUMPTION_VISIBLE=PASS  
PROVIDER_OPERABILITY_VISIBLE=PASS  

## Wave 5 — Agent Config / Unified Runtime / Diagnostics

### Objective
Productize AI operations and make Unified Runtime the enforced execution path for all formal Agents.

### Scope
- Overall → System Settings → Agent Configuration.
- Provider / Model / SecretRef.
- Agent Binding.
- Connectivity Test.
- Agent Smoke Test.
- Effective Config.
- Config Revision / Audit.
- Rollback.
- Runtime Effective Config Snapshot.
- Unified Runtime enforcement.
- no silent legacy fallback.
- Runtime Diagnostics center.
- normalized failure codes and recent run/step/attempt/retry visibility.
- Knowledge Production failure-step diagnostics.

### Selective absorb review
SA-01 Runtime strict structured-output / semantic-handoff hardening.  
SA-02 Provider/Runtime operability evidence patterns.  
No whole branch merge.

### Domain minimal fix
DF-06 Unified Runtime / Config.

### Architect decision
ARCH-05 unified-agent-config / SecretRef / Effective Config / bootstrap precedence / rollback.

### UED deltas
UED-05 Agent Configuration Center.  
UED-06 Runtime Diagnostics Center.

### Scenario closure
SCN-R2-17 / 18 / 19.

### DoD
AGENT_CONFIG_CENTER=PASS  
SECRET_PLAINTEXT_EXPOSURE=NO  
EFFECTIVE_CONFIG=PASS  
CONFIG_REVISION=PASS  
ROLLBACK=PASS  
CONNECTIVITY_TEST=PASS  
AGENT_SMOKE=PASS  
FORMAL_AGENT_UNIFIED_RUNTIME=100%  
SILENT_LEGACY_FALLBACK=0  
RUNTIME_DIAGNOSTICS=PASS  

## Wave 6 — Trust / Compatibility / Release

### Objective
Close cross-product trust contracts and produce a complete, safe, testable R2 candidate.

### Scope
- common-evidence/v1.0 additive compatibility.
- overall-return-context/v1 additive compatibility.
- Evidence → Source → Version/Locator → Return.
- Existing route / legacy / scenario / portrait protection.
- Projection parity protection.
- Candidate Preflight:
  Runtime Availability / Provider Connectivity / Agent Binding / Data Root Writable / Critical API / Real AI Smoke / Formal KP Smoke.
- Windows package.
- Single startup / stop.
- Manifest / Source Commit / SHA256.
- clean target deploy.
- post-deploy smoke.
- external data/config safety floor.
- rollback + rollback validation.
- TSE intake / DUT rebind.

### Domain minimal fix
Release Engineering, plus targeted domain adapter fixes only if compatibility evidence exposes a defect.

### Architect decision
ARCH-06 Data Safety / migration-failure / rollback-recovery contract.

### Scenario closure
SCN-R2-20 / 21 plus final regression of SCN-R2-01..19.

### DoD
COMMON_EVIDENCE_SOURCE_RETURN=PASS  
ROUTE_COMPATIBILITY_GATE=PASS  
SCENARIO_PORTRAIT_PROTECTION=PASS  
CANDIDATE_PREFLIGHT=PASS  
WINDOWS_TARGET_DEPLOYMENT=PASS  
DATA_SAFETY_FLOOR=PASS  
ROLLBACK_VALIDATION=PASS  
MANDATORY_SCENARIOS=21/21_BOUND  
COMPLETE_PRODUCT_CANDIDATE=YES  
READY_FOR_TSE=YES  

## UED handoff

UED_DELTA_COUNT=6

- UED-01 Common Problem View / Current Problem Landing.
- UED-02 Major Excel production maintenance.
- UED-03 Storage Matrix / Device Context.
- UED-04 Storage Knowledge single flow / Operability.
- UED-05 Agent Configuration Center.
- UED-06 Runtime Diagnostics Center.

UED must preserve frozen product semantics. UED does not redefine Domain ownership, state machines, contracts, permissions or Expected.

## Domain Owner handoff

DOMAIN_FIX_COUNT=6

- DF-01 Existing Problem.
- DF-02 Major Case.
- DF-03 Hardware Case.
- DF-04 Storage.
- DF-05 Unified Knowledge.
- DF-06 Unified Runtime / Config.

Minimal-fix rule: only the smallest domain-owned change necessary to satisfy the frozen public contract. No adjacent redesign.

## Architect review handoff

ARCH_DECISION_COUNT=6

- ARCH-01 Canonical Problem Identity / Relation.
- ARCH-02 Trusted Authorization / Audit Actor.
- ARCH-03 ProjectDeviceContext.
- ARCH-04 Storage ↔ Knowledge projection.
- ARCH-05 Unified Agent Config / Runtime Enforcement.
- ARCH-06 Release Data Safety.

Architect review is required before implementation of the dependent contract-breaking or identity/security boundary. Existing additive work may continue where it does not pre-empt the decision.

## Milestone synchronization

- W1 closure: focused Current Problem regression.
- W2 closure: Major Golden Path + Repeat regression.
- W3 closure: Hardware consumer/maintainer/security regression.
- W4 closure: Storage main-flow + Knowledge real E2E.
- W5 closure: Agent Config + Runtime + Diagnostics real provider smoke.
- W6 closure: full mandatory scenario gate + Windows clean deploy + rollback + TSE intake.

Sync current main only by explicit release-engineering decision and with regression proof. No silent rebasing of frozen candidate evidence.

## Planning conclusion

PLANNING_RESULT=PASS  
BLOCKER_COUNT=0  
KNOWN_ENV_DEPENDENCY=GitHub #260 Real Provider environment validation; this is a release-environment dependency, not a Wave 1 planning blocker.  
WAVE1_FIRST_TASK=R2-W1-CANONICAL-PROBLEM-CONTRACT-AND-FOUR-WORKBENCH-CLOSURE-001  
NEXT=WAVE1_EXECUTION
