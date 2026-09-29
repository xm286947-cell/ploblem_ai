# Overall R2 W1 Canonical Problem Contract V1

TASK=R2-W1-CANONICAL-PROBLEM-CONTRACT-AND-FOUR-WORKBENCH-CLOSURE-001
ISSUE=#267
STATUS=FROZEN_FOR_W1
CONTRACT=canonical-problem/v1
RELATION_CONTRACT=canonical-problem-relation/v1
SOURCE_REF_CONTRACT=source-problem-itr-ref/v1

## 1. Targeted Verification Result

This verification is intentionally bounded to W1 and does not reopen the global Existing Capability Audit.

1. Existing Problem identity
   - quality_issue.knowledge_id is the persisted master object key.
   - (business_type, business_issue_id) is the persisted business uniqueness boundary.
   - ITR public identity is normalized only by source-problem-itr-ref/v1.
   - W1 creates no second Problem table or master.

2. ITR / Resolution
   - Real entries are /p0/itr-recovery and /p0/itr-resolution (legacy compatibility: /itr/recovery-workbench, /itr/resolution-workbench).
   - Resolution source facts come from ITR-CS.
   - No authoritative source-domain Save / Submit / Transition contract is present in this repository. W1 therefore freezes the projection as SOURCE_OWNED_READ_ONLY; source actions remain unbound rather than invented.

3. Software assessment
   - Real entry is /p0/software-assessment (legacy compatibility: /software-assessment).
   - Source facts come from SW-OPS / SOFTWARE_OPERATION.
   - /analysis is the AI analysis capability and is not Software Assessment.
   - No authoritative source-domain mutation contract is present in this repository; W1 keeps the assessment projection read-only.

4. Missed-test
   - Real entry is /p0/missed-test-analysis (legacy compatibility: /missed-test-analysis).
   - Relation exists only when Existing Problem source fact escape.is_escape explicitly marks the issue as missed-test.
   - No second missed-test master is created.

5. Cross-workbench relation
   - Persisted issue_material_link is authoritative.
   - Historical recovery may project an exact, unique canonical ITR match read-only.
   - Ambiguous, missing, invalid or fuzzy matches are never promoted to relations.
   - Title/product/description similarity is not a relation key.

6. Deep Link / Return
   - W1 keeps the four real workbench routes.
   - Return targets are same-origin, route-allowlisted, and preserve supported query/filter state.
   - External return targets fail closed.

## 2. ARCH-01 Decision

OWNER_DOMAIN=EXISTING_PROBLEM
SECOND_PROBLEM_MASTER=NO
COMMON_PROBLEM_VIEW_IS_WORKBENCH=NO
BUSINESS_WORKBENCH_COUNT=4

canonical-problem/v1 is a projection contract over the existing quality_issue master.

Canonical identity:
- canonical_problem_id = BUSINESS_TYPE:canonical-ITR
- master_object_ref = EXISTING_PROBLEM / quality_issue / knowledge_id
- business_ref = business_type + business_issue_id
- source_problem_ref = source-problem-itr-ref/v1
- version_ref = issue_version_id + version_no

A non-ITR or invalid public ref is not silently promoted to a Canonical Problem Identity.

## 3. Relation Contract

Every relation exposes:
- relation_contract_version
- relation_policy=EXPLICIT_OR_EXACT_UNIQUE_ONLY
- key
- relation_status
- canonical_problem_id + canonical_problem_ref
- source_domain
- structured source_refs (material/source object, business key, material version, hash and locator where available)
- version_ref
- access_mode=READ_ONLY_PROJECTION
- permission_authority=SOURCE_DOMAIN
- return_context_contract=overall-return-context/v1
- href only when the relation is proven
- no_relation_reason when the relation is not proven

Allowed relation keys:
1. ITR
2. RESOLUTION
3. SOFTWARE_ASSESSMENT
4. MISSED_TEST

Missing or ambiguous relation:
relation_status=NO_RELATION, href is empty.

## 4. Common Problem View

Common Problem View is the shared consumption/navigation surface over Existing Problem. It may aggregate the four workbench relations, Repeat Risk, Historical Case, analysis and Evidence, but it does not own a business workflow or source-domain transition.

No source-domain Save / Submit / Transition is introduced by this contract.

## 5. W1 Compatibility Floor

The following semantics remain frozen:
- /analysis remains AI analysis, not Software Assessment.
- legacy /issues remains available.
- material import pages are intake/browse surfaces, not business workbenches.
- source-owned state/action/permission semantics are not fabricated.
- integration/overall-vnext is not merged wholesale.

NEXT=W1_FOCUSED_REGRESSION


## 6. Public Projection / W1 Closure Evidence

Unified Overall host exposes a read-only projection:

`GET /api/v2/issues/{knowledge_id}/workbench-relations`

The endpoint reads Existing Problem plus source-material relations only. It does
not persist a relation during reads and does not provide mutation endpoints.

W1 focused regression includes an ambiguity case where the same normalized ITR
exists in two business-type uniqueness domains. Resolution and Software
Assessment must return `NO_RELATION`, empty `href`, empty `source_refs` and
`RELATION_NOT_FOUND_OR_AMBIGUOUS`; no candidate may be selected by recency,
title, product or insertion order.
