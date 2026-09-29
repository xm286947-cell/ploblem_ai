# R2 W5 Agent Config / Unified Runtime Enforcement / Runtime Diagnostics Contract V1

TASK: `R2-W5-AGENT-CONFIG-RUNTIME-ENFORCEMENT-AND-DIAGNOSTICS-CLOSURE-001`

Status: **IMPLEMENTATION_BASELINE**

Source base: `e47dbe16811bd99e8fe08f290d1d9bc28ac0a467`

## Direct Reuse

W5 reuses, without duplication:

- `runtime/config/*` and AGENT-CONFIG-001;
- `config/runtime/model.yaml` and canonical Agent YAMLs;
- Unified Runtime Provider execution, retry, hard-cap, checkpoint/resume and execution snapshots;
- `SqliteTaskStore` Task / Run / Step / Attempt / Checkpoint / Commit trace;
- `contracts/runtime_binding/v1` formal binding manifest.

W5 MUST NOT create a second Runtime, Provider stack, trace store, retry engine or product-private Agent Config center.

## Common Routes

- Overall Agent Config: `/p0/system/agent-config`
- Agent Config API: `/api/v2/system/agent-config`
- Overall Runtime Diagnostics: `/p0/system/runtime-diagnostics`
- Runtime Diagnostics API: `/api/v2/system/runtime-diagnostics`

These are platform-owned projections/control-plane routes. Existing `/p0/settings` remains product/field mapping and is not renamed or impersonated.

## Secret Boundary

Control-plane writes are SecretRef-only.

Literal fields such as `api_key`, password, token or secret are rejected for revisions. The UI/API may expose:

- `api_key_env`;
- secret present/not-present;
- endpoint env reference;
- provider/model identity.

It MUST NOT expose the resolved secret value.

Existing Runtime remains the only execution-time secret resolver/injector.

## Revision / Audit / Rollback

Revision state is stored below the external Overall Runtime Control Root, not in repository source.

Default:

`<R2 data root>/overall_runtime_control`

Override:

`OVERALL_RUNTIME_CONTROL_ROOT`

Revision activation drives the existing domain config override entry points:

- `MAJOR_MODEL_CONFIG`
- `HARDWARE_CASE_MODEL_CONFIG`
- `STORAGE_MODEL_CONFIG`

Explicit operator environment overrides remain authoritative.

Activation and rollback apply to future Runtime builds/restart; W5 does not implement unsafe hot mutation of already-running execution snapshots.

## Formal Agent Enforcement

The formal W5 set is exactly the BOUND manifest:

- MAJOR_ISSUE → `major_issue.v2.occurrence`
- HARDWARE_CASE → `hardware_case.structure`
- REVERSE_QUALITY → `reverse_quality.analysis`
- STORAGE → `storage.emmc.parameter_extract`
- KNOWLEDGE → `knowledge.production.extract`

For this set:

- provider execution owner = Runtime;
- retry owner = Runtime;
- secret resolution owner = Runtime;
- execution snapshot owner = Runtime;
- direct provider calls = false;
- domain retry loops = false;
- secret persistence = false;
- silent legacy fallback = forbidden.

Historical V2 compatibility stages outside this manifest are not silently promoted into the formal set by W5.

## Connectivity / Agent Smoke

Connectivity Test uses a diagnostic-only Agent executed through `ConfiguredAgentRuntime` and the existing Runtime Provider adapter. It does not create another probe stack.

Agent Smoke validates that a requested Agent belongs to the formal binding set and that its selected Model / Provider / endpoint ref / SecretRef are currently resolvable.

Real-provider final release proof remains W6/TSE-preflight evidence and is not inferred from mock/config-only W5 evidence.

## Runtime Diagnostics

Overall Runtime Diagnostics opens existing Runtime SQLite stores read-only and projects:

Task → Run → Step → Attempt, including retry coordinates, provider-call sequence, error code/category, and persisted execution metrics.

It does not copy Runtime records into a second trace store.

Knowledge Production failure diagnostics are a filtered projection of the same existing Runtime records.

## W4 Closure

Storage Provider Operability now deep-links to:

- `/p0/system/agent-config`
- `/p0/system/runtime-diagnostics`

No dead link and no Storage-private configuration/diagnostics center is created.

## Public Contract

`PUBLIC_CONTRACT_CHANGE=NO`

This W5 implementation adds an Overall control-plane projection and governance state only. Existing Runtime and domain public execution contracts remain unchanged.
