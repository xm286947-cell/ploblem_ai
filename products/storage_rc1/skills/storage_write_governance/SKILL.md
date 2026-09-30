# storage-write-governance

## Purpose
Identify software-write behaviors that may increase storage wear and surface evidence-backed engineering controls and validation needs.

## Knowledge Pack
Consumes **PACK_WRITE_GOVERNANCE** as a filtered/versioned view of the current Formal Knowledge Release. This skill does not read Knowledge Production internal storage and does not create a second knowledge store.

## Execution boundary
- Use existing Unified Runtime/provider boundaries.
- Use existing Formal Knowledge Release consumer and Evidence refs.
- Use existing deterministic Lifetime Engine / Software Impact capability when applicable.
- Unreleased knowledge is never formal evidence.
- Missing evidence, missing protocol semantics, stale runtime data, or unsupported deterministic model must fail closed.

## Inputs
- `device_type`: string
- `confirmed_device_facts`: array
- `workload_software_facts`: array
- `user_context`: object

## Outputs
- `observed_or_declared_behavior`
- `potential_mechanism`
- `potential_risk`
- `engineering_control_options`
- `conditions_and_limits`
- `missing_information`
- `suggested_validation`
- `evidence_refs`
- `confidence_basis`
- `review_roles`

## Rules
- FORMAL_KNOWLEDGE_ONLY
- NO_OPAQUE_RISK_SCORE
- NO_SPECIFIC_LIFETIME_LOSS_WITHOUT_SUPPORTED_MODEL
- UNKNOWN_IS_NOT_SAFE

## Review
All engineering conclusions remain reviewable. This skill never makes an automatic replacement approval/rejection decision.
