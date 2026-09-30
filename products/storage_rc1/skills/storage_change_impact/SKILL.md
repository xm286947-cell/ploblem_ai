# storage-change-impact

## Purpose
Compose confirmed Device A/B parameter deltas into evidence-backed lifetime/software/monitoring impact and validation requirements.

## Knowledge Pack
Consumes **PACK_CHANGE_IMPACT** as a filtered/versioned view of the current Formal Knowledge Release. This skill does not read Knowledge Production internal storage and does not create a second knowledge store.

## Execution boundary
- Use existing Unified Runtime/provider boundaries.
- Use existing Formal Knowledge Release consumer and Evidence refs.
- Use existing deterministic Lifetime Engine / Software Impact capability when applicable.
- Unreleased knowledge is never formal evidence.
- Missing evidence, missing protocol semantics, stale runtime data, or unsupported deterministic model must fail closed.

## Inputs
- `confirmed_facts_a`: array
- `confirmed_facts_b`: array
- `parameter_delta`: array
- `application_workload_context`: object

## Outputs
- `parameter_changes`
- `technical_meaning`
- `lifetime_impact`
- `software_impact`
- `diagnostic_monitoring_impact`
- `validation_requirements`
- `evidence_refs`
- `unknowns`
- `review_roles`
- `impact_classification`

## Rules
- NO_AUTO_APPROVE_REJECT
- UNKNOWN_IS_NOT_SAFE
- CLASSIFICATION_ENUM_ONLY
- FORMAL_KNOWLEDGE_ONLY

## Review
All engineering conclusions remain reviewable. This skill never makes an automatic replacement approval/rejection decision.
