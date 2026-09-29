# R2 W4 Storage Direct-Reuse Contract V1

TASK: `R2-W4-STORAGE-DIRECT-REUSE-AND-BUSINESS-FLOW-CLOSURE-001`

Status: **IMPLEMENTATION_BASELINE**

Integration base: `6658d2e13cfbe15f62d2f6c9724bfd33415e5bd3`

## 1. Direct-Reuse Decision

W4 MUST reuse the existing Storage product mounted at `/storage-workspace/`.

It MUST NOT create:
- a second Storage app, database or port;
- a second Device master;
- a second Matrix/Lifetime/Diagnosis/Change-Impact engine;
- a second Knowledge Production or Formal Source Intake;
- a second Runtime or Provider stack;
- Storage-private Agent Config or Runtime Diagnostics.

Existing P01-P08 remain the product UI. W4 may only add small projections to those pages.

## 2. ARCH-03 ProjectDeviceContext

`ProjectDeviceContext` is a projection only:

```
ProjectRef
  -> existing links(target_system="project", target_id=ProjectRef)
  -> existing devices.id
  -> existing Device Fact / Lifetime / Diagnosis / Impact / Evidence
```

There is no Project table and no new Device table.

Product projection:
- `GET /api/product/projects/{project_ref}/context`
- `POST /api/product/projects/{project_ref}/matrix`

The matrix endpoint scopes devices by existing links and then directly calls the existing
`product_api.compare_devices()` engine.

Existing `POST /api/links` is extended only to accept `target_system=project`.

## 3. Cross-Domain Status Projection

Project/Device projections use the common product fields:

- Current Status
- Failure Reason
- Next Action
- Owner Role
- Evidence
- Version

These are derived views. They do not own or mutate source-domain state machines.

## 4. ARCH-04 Storage <-> Unified Knowledge

Canonical Storage knowledge maintenance flow is:

```
Formal SourceDocument Intake
-> Unified Runtime Extraction
-> Candidate / Evidence / Evaluation
-> Human Review
-> Publish
-> Formal Knowledge Release
-> KnowledgeReleaseConsumer
-> Storage consumption
```

Canonical source intake:
`/api/product/knowledge-production/sources`

Legacy local source intake:
`/api/v1/knowledge/sources`

Legacy intake remains **SUPPORTING_LEGACY** only and cannot publish Formal Knowledge.

No Unified Knowledge public contract change is required.

## 5. Provider Operability

Product projection:
`GET /api/product/provider-operability`

It may read only:
- existing Runtime status;
- existing Runtime execution history.

It exposes:
- Provider Configured
- Provider/Profile
- Model Binding
- API Key Present flag
- Connectivity derived from latest execution
- Last Check
- Last Error
- Current Status / Failure Reason / Next Action / Owner / Version

It MUST NOT:
- issue a new Provider probe;
- execute Provider HTTP directly;
- own Provider secrets/config;
- duplicate Runtime diagnostics.

Runtime execution events may add a timestamp as presentation metadata only; Runtime execution semantics do not change.

## 6. Overall Common Capability Dependency

The current source does not contain a verified formal destination for:

- Overall -> Agent Config
- Overall -> Runtime Diagnostics

W4 therefore returns those deep links as `DEPENDENCY_PENDING` with `href=null`.

No dead link may be invented.

This dependency does not block Storage domain implementation, but remains an external dependency for the final navigation DoD.

## 7. Protected Boundaries

Unchanged by design:
- Canonical Problem contracts
- Common Evidence public contract
- Unified Runtime execution semantics
- Provider stack
- Agent Config
- Storage device schema
- Lifetime engine
- Engineering insight/diagnosis
- Software impact/change-impact engine
- Knowledge Production state machine
- Formal Knowledge Release contract

PUBLIC_CONTRACT_CHANGE=NO
SECOND_DEVICE_MASTER=NO
SECOND_KNOWLEDGE_PRODUCTION=NO
SECOND_RUNTIME=NO
PRIVATE_PROVIDER_STACK=NO
