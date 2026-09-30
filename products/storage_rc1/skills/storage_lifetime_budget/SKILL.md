# storage-lifetime-budget

## Purpose
Compose confirmed endurance facts and runtime/workload facts through the existing deterministic Lifetime Engine.

## Knowledge Pack
Consumes **PACK_LIFETIME_ENGINEERING** as a filtered/versioned view of the current Formal Knowledge Release. This skill does not read Knowledge Production internal storage and does not create a second knowledge store.

## Execution boundary
- Use existing Unified Runtime/provider boundaries.
- Use existing Formal Knowledge Release consumer and Evidence refs.
- Use existing deterministic Lifetime Engine / Software Impact capability when applicable.
- Unreleased knowledge is never formal evidence.
- Missing evidence, missing protocol semantics, stale runtime data, or unsupported deterministic model must fail closed.

## Inputs
- `device_type`: string
- `confirmed_endurance_facts`: array
- `target_service_life`: object
- `runtime_workload_facts`: array
- `requested_metric`: string

## Outputs
- `applicable_formula`
- `endurance_basis`
- `write_budget`
- `measured_vs_budget`
- `margin_status`
- `assumptions`
- `missing_information`
- `evidence_refs`
- `formula_replay_refs`

## Rules
- DETERMINISTIC_FORMULA_FIRST
- EXISTING_LIFETIME_ENGINE_ONLY
- NO_FAKE_REMAINING_YEARS
- ASSUMPTIONS_EXPLICIT
- UNSUPPORTED_MODEL_FAIL_CLOSED

## Review
All engineering conclusions remain reviewable. This skill never makes an automatic replacement approval/rejection decision.
