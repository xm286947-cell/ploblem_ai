# R2_CAPABILITY_SCOPE_MATRIX_V1.2

TASK=OVERALL-R2-CAPABILITY-SCOPE-AND-IMPLEMENTATION-PLAN-001  
STATUS=FROZEN_FOR_WAVE_EXECUTION  
PRODUCT_INPUT=OVERALL_R2_PRODUCT_REQUIREMENTS_AND_APPLICATION_SCENARIOS_V1.2_20260929  
PRODUCT_INPUT_URL=https://docs.google.com/document/d/1coYbHindeInKu5SeQ3MF3yQKhvVNZA2gHrhp4Ee3bXY/edit  
DEVELOPMENT_PLAN_URL=https://docs.google.com/document/d/1J5pTRV9MUnKRnMhFShBbla0w6IsZ1npxkdif73xvSkE/edit  
SOURCE_MAIN_SHA=86e5e21e16e33c81587260eb43eb3cf85994e02d  
R2_BRANCH=integration/overall-r2  
R2_HEAD_AT_ANALYSIS=d2e806d3442515b8a2b04f759b2be4ee63b3f0bd  
GLOBAL_EXISTING_CAPABILITY_AUDIT_REOPEN=NO  
TARGETED_VERIFICATION_ONLY=YES  

## 1. Count semantics

Primary disposition is mutually exclusive and totals 11 product requirements. Secondary implementation modes may coexist on the same requirement.

PRIMARY_DIRECT_REUSE_COUNT=2  
PRIMARY_ADAPTER_COUNT=3  
PRIMARY_ROUTE_COMPATIBILITY_COUNT=1  
PRIMARY_PUBLIC_CONTRACT_COUNT=1  
PRIMARY_SELECTIVE_ABSORB_COUNT=1  
PRIMARY_NEW_IMPLEMENTATION_COUNT=3  
PRIMARY_TOTAL=11  

DIRECT_REUSE_TOUCH_COUNT=9  
ADAPTER_TOUCH_COUNT=4  
ROUTE_COMPATIBILITY_TOUCH_COUNT=2  
PUBLIC_CONTRACT_TOUCH_COUNT=10  
SELECTIVE_ABSORB_TOUCH_COUNT=2  
NEW_IMPLEMENTATION_TOUCH_COUNT=3  

## 2. P0 / Protection Requirement Scope Matrix

| ID | Requirement | Primary Disposition | Secondary Modes | Current R2 Baseline | R2 V1.2 Delta | Wave | UED | Domain Minimal Fix | Architect Decision |
|---|---|---|---|---|---|---|---|---|---|
| R2-PR-01 | Canonical Problem Identity + Common Problem View + four workbenches | Existing Page Adapter | Direct Reuse + Public Contract | W1 adapters, cross-workbench association recovery, Common Problem View direction, Return Context already exist | Freeze long-term canonical identity/relation/deep-link/return/fail-closed contract; close source-owned action bindings only where authoritative | W1 | YES | Existing Problem | YES |
| R2-PR-02 | Major Case Excel first-class Golden Path | Existing Direct Reuse | Page Adapter + Public Contract | #253/#257 restored Excel Upload→Preview→Mapping→Confirm→Import→Source Fact and continuation | Add template_version, mapping_version, import batch/run governance and explicit compatibility/fail-fast semantics | W2 | YES | Major Case | NO |
| R2-PR-03 | Hardware CONSUMER/MAINTAINER + nav/route/API auth + audit | Existing Page Adapter | Direct Reuse + Public Contract | #261 restored maintenance entry and consumer fail-closed; existing P01-P07 and APIs retained | Bind trusted server-side role source; freeze Navigation/Route/API authorization contract; add maintenance audit records | W3 | NO | Hardware Case | YES |
| R2-PR-04 | Storage ProjectDeviceContext / Matrix business flow | New Implementation | Direct Reuse + Public Contract | Existing Storage product, device facts, lifetime, observation, diagnosis, impact and compare capabilities exist | Add ProjectDeviceContext projection + Matrix landing + action-oriented business orchestration; no second Device master | W4 | YES | Storage | YES |
| R2-PR-05 | Storage Knowledge single user flow | Existing Page Adapter | Direct Reuse + Public Contract | Formal Knowledge Production and Storage consumption exist; current UX exposes split source verification / formal production concepts | One visible maintenance flow, one intake, status/failure/next-action projection across Unified Knowledge + Storage | W4 | YES | Storage + Unified Knowledge | YES |
| R2-PR-06 | Unified Agent Configuration Center | New Implementation | Public Contract | Runtime model/agent YAML + env bootstrap exist; no productized front-end control plane | Build Overall settings page + backend config revision, SecretRef, Effective Config, rollback, connectivity and smoke | W5 | YES | Runtime / Config | YES |
| R2-PR-07 | Unified Runtime Enforcement | Selective Absorb from overall-vnext | Direct Reuse + Public Contract | Unified Runtime exists; R2 Storage runtime bridge and candidate binding exist | Prohibit silent legacy fallback for formal Agents; selectively review vnext strict-json/semantic-handoff/provider/runtime hardening rather than whole-branch merge | W5 | NO | Runtime | YES |
| R2-PR-08 | AI Provider Operability + Runtime Diagnostics | New Implementation | Selective Absorb + Public Contract | Runtime trace/evidence and product-local diagnostics primitives exist; no unified operator-facing diagnostics center | Build Overall Runtime Diagnostics + normalized error/status projection; Storage shows context card and deep-links only | W4/W5 | YES | Runtime + Storage adapter | NO (covered by PR-06/07 architecture decision) |
| R2-PR-09 | Common Evidence / Source / Deep Link / Return Contract | Public Contract | Direct Reuse + Route Compatibility | W2 overall-return-context/v1 and W3 common-evidence/v1.0 already pass | Evolve compatibly for permission/version/status/source-locator and canonical-problem relation return; no producer DB reads | W6 | NO | Domain adapters only | NO |
| R2-PR-10 | Existing Capability Compatibility + Scenario/Portrait Protection | Route Compatibility | Direct Reuse | W1/W2 legacy routes, scenario/portrait compatibility and projection parity are implemented | Extend frozen compatibility gate to full V1.2 scope; targeted verification only when route binding is unclear | W6 | NO | QS only if parity defect appears | NO |
| R2-PR-11 | Formal Release Engineering + Candidate Preflight + Data Safety | Existing Direct Reuse | Public Contract | W5 Windows build/start/manifest/source binding/smoke/rollback infrastructure exists | Extend preflight to Runtime/Provider/Agent/DataRoot/Critical API/AI/KP smoke; freeze external data/config safety floor | W6 | NO | Release Engineering | YES |

## 3. Selective Absorb Boundary

WHOLE_OVERALL_VNEXT_MERGE=FORBIDDEN  
TARGETED_DIFF_REVIEW_REQUIRED=YES  

Approved for targeted diff review only:

1. SA-01 Runtime strict structured-output / semantic failure handoff stack:
   - runtime/config/*
   - runtime/contracts/models.py
   - runtime/engine/runtime.py
   - runtime/providers/openai_compatible.py
   - runtime/store/sqlite.py
   - related strict-json / semantic-handoff tests.
2. SA-02 Provider/Runtime operability evidence:
   - OpenAI Mock / real-provider validation patterns
   - runtime provider adapter tests
   - Storage runtime mock integration tests.

Absorb only if the R2 implementation does not already provide the same semantics. Re-implementation is preferred when the vnext diff conflicts with newer R2 contracts or packaging.

## 4. 21 Mandatory Scenario → Implementation Mapping

| Scenario | Requirement | Wave | Implementation Scope / Proof |
|---|---|---|---|
| SCN-R2-01 Canonical Problem Identity | PR-01 | W1 | one canonical identity, no fifth problem master |
| SCN-R2-02 Relation + No Relation Fail-Closed | PR-01 | W1 | exact relation only; NO_RELATION on missing/ambiguous |
| SCN-R2-03 Deep Link / Return Context | PR-01/09 | W1/W6 | query/filter/tab/selection/scroll/anchor preserved |
| SCN-R2-04 ITR→Repeat→Historical Case→Return | PR-01/02/09 | W1/W2/W6 | cross-domain deep link + evidence + human return |
| SCN-R2-05 Software Assessment / Missed-Test real workbench | PR-01/10 | W1 | real existing capability; no /analysis substitution |
| SCN-R2-06 Major Excel Batch Intake | PR-02 | W2 | template/version→preview→mapping→confirm→import→source fact |
| SCN-R2-07 Excel→AI→Review→Publish→Repeat | PR-02 | W2 | same production chain; atomic/fail-closed |
| SCN-R2-08 Major single/batch convergence | PR-02 | W2 | single event and batch meet at Source Fact |
| SCN-R2-09 Hardware Consumer | PR-03 | W3 | published-only P01/P02/P03/P04/P06 |
| SCN-R2-10 Hardware Maintainer Navigation | PR-03 | W3 | trusted role controls maintenance visibility |
| SCN-R2-11 Hardware Route/API Authorization | PR-03 | W3 | manual URL/API/query escalation denied |
| SCN-R2-12 Hardware Maintenance Audit | PR-03 | W3 | actor/timestamp for confirm/publish/tree/mapping mutations |
| SCN-R2-13 Hardware P07 Tree Import | PR-03 | W3 | mapping/validation/diff/conflict/apply/version atomic fail-closed |
| SCN-R2-14 Storage ProjectDeviceContext Main Flow | PR-04 | W4 | Matrix→Device→Fact→Lifetime/Observation→Diagnosis→Impact→Decision |
| SCN-R2-15 Storage Knowledge Unified Flow | PR-05 | W4 | one intake + cross-domain status projection + formal release consumption |
| SCN-R2-16 Storage AI Provider Operability | PR-08 | W4 | Storage context status card; jump to Overall settings/diagnostics |
| SCN-R2-17 Agent Config | PR-06 | W5 | Provider/Model/SecretRef/Binding/Connectivity/Save |
| SCN-R2-18 Unified Runtime Enforcement | PR-07 | W5 | effective config→runtime→provider/model→run/step/attempt trace |
| SCN-R2-19 Runtime Diagnostics | PR-08 | W5 | normalized diagnostics for config/provider/model/timeout/structured/KP failures |
| SCN-R2-20 Evidence/Source/Return + Legacy Compatibility | PR-09/10 | W6 | cross-product evidence and legacy deep-link compatibility gate |
| SCN-R2-21 Candidate/Preflight/Deploy/Rollback | PR-11 | W6 | build→preflight→clean deploy→smoke→TSE→rollback with data safety |

MANDATORY_SCENARIO_COUNT=21  
MANDATORY_SCENARIO_BOUND=21  
UNBOUND_MANDATORY_SCENARIO=0  

## 5. UED Delta

UED_DELTA_COUNT=6

1. UED-01 Current Problem Landing / Common Problem View: show canonical issue + four business workbenches without presenting Common View as a fifth workbench.
2. UED-02 Major Case Excel maintenance: template/mapping/batch/run/version states and failure details.
3. UED-03 Storage Project / Device Matrix + Device Context main flow.
4. UED-04 Storage Knowledge single maintenance flow + Storage AI Operability status card.
5. UED-05 Overall System Settings → Agent Configuration Center.
6. UED-06 Overall System Settings → Runtime Diagnostics Center.

No mandatory UED redesign for Hardware current pages, Common Evidence viewer, legacy scenario/portrait compatibility, or Windows release tooling unless focused usability defects are found.

## 6. Domain Owner Minimal Fixes

DOMAIN_FIX_COUNT=6

1. DF-01 Existing Problem: authoritative source action/permission binding where currently external, plus canonical relation contract compatibility.
2. DF-02 Major Case: template_version / mapping_version / import batch-run persistence and error semantics.
3. DF-03 Hardware Case: trusted host role resolver + maintenance audit event persistence; preserve domain ownership.
4. DF-04 Storage: ProjectDeviceContext projection and Matrix aggregation without a second Device master.
5. DF-05 Unified Knowledge: status/release/evidence projection needed by the single Storage maintenance flow; no duplicate production logic.
6. DF-06 Unified Runtime: Config Revision / SecretRef / Effective Config Snapshot / diagnostics APIs and formal-agent no-silent-fallback enforcement.

Domain fixes must remain minimal and contract-oriented; business state machines stay in the owning domain.

## 7. Overall Architect Contract Decisions

ARCH_DECISION_COUNT=6

1. ARCH-01 canonical-problem-identity + cross-workbench-relation ownership/versioning and stable Object Ref.
2. ARCH-02 trusted authorization context contract for Hardware route/API/navigation decisions and audit actor identity.
3. ARCH-03 ProjectDeviceContext identity/ref contract and its relationship to existing Storage Device facts; no second Device master.
4. ARCH-04 Storage ↔ Unified Knowledge status/release/evidence projection boundary and failure semantics.
5. ARCH-05 unified-agent-config / SecretRef / Effective Config Snapshot + Runtime Enforcement boundary, including bootstrap YAML/env precedence and rollback.
6. ARCH-06 Release Data Safety contract: external Data Root, schema/config version recognition, migration-failure fail-closed and rollback recovery.

Existing common-evidence/v1.0 and overall-return-context/v1 remain backward-compatible baselines; evolve additively unless a breaking change is proven necessary.

## 8. Explicit Non-Scope / Audit Rule

- DO_NOT_REOPEN_GLOBAL_EXISTING_CAPABILITY_AUDIT.
- Route Unknown != Capability Absent.
- Unknown bindings use Targeted Verification only.
- No second Problem master, Device master, Knowledge Production, Runtime, Provider stack, Web app or port.
- No whole merge of integration/overall-vnext.
- No product action/state fabrication when the authoritative owner is external.

## 9. Wave Entry

WAVE1_FIRST_TASK=R2-W1-CANONICAL-PROBLEM-CONTRACT-AND-FOUR-WORKBENCH-CLOSURE-001  
PLANNING_BLOCKER_COUNT=0  
KNOWN_RELEASE_ENV_DEPENDENCY=GitHub #260 real-provider environment validation; does not block W1 planning/execution.  
NEXT=R2_IMPLEMENTATION_PLAN_V1.2
