# storage-diagnostic-validation

## Purpose
Describe measurable storage diagnostics, acquisition source, interpretation boundary and validation method.

## Knowledge Pack
Consumes **PACK_DIAGNOSTIC_VALIDATION** as a filtered/versioned view of the current Formal Knowledge Release. This skill does not read Knowledge Production internal storage and does not create a second knowledge store.

## Execution boundary
- Use existing Unified Runtime/provider boundaries.
- Use existing Formal Knowledge Release consumer and Evidence refs.
- Use existing deterministic Lifetime Engine / Software Impact capability when applicable.
- Unreleased knowledge is never formal evidence.
- Missing evidence, missing protocol semantics, stale runtime data, or unsupported deterministic model must fail closed.

## Inputs
- `device_type`: string
- `diagnostic_capabilities`: array
- `runtime_observations`: array
- `target_question`: string

## Outputs
- `supported_metrics`
- `acquisition_method`
- `data_source`
- `current_observation`
- `interpretation_boundary`
- `abnormality_signal`
- `validation_method`
- `missing_information`
- `evidence_refs`

## Rules
- CAPABILITY_IS_NOT_RUNTIME_VALUE
- STALE_OBSERVATION_NOT_CURRENT_EVIDENCE
- FORMAL_SEMANTICS_REQUIRED
- FAIL_CLOSED

## Review
All engineering conclusions remain reviewable. This skill never makes an automatic replacement approval/rejection decision.
