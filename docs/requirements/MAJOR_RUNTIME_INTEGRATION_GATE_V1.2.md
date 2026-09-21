# Major Issue → Unified Agent Runtime Integration Gate V1.2

Status: **CODE_GATE_PASS / LIVE_PROVIDER_ACCEPTANCE_PENDING**  
Date: 2026-09-21  
Business domain: `MAJOR_CASE`

## 1. Current baselines

- Major integration branch:
  - `integration/major-runtime-v1.1@542b4e9ce7171b8b3107ab80e32774cb1fdc2dca`
- Major original-mechanism restore:
  - PR #21 merged into integration
  - merge commit `c1d0cb6e698481cc37cb204088460b830097d7af`
- Unified Runtime ORCH-B01:
  - PR #22 merged to `main`
  - Runtime main merge commit `f98a8922cc7a946c7bc43265ed9c6b1f8d386d5b`
- Runtime → Major integration synchronization:
  - PR #23 merged
  - integration sync merge commit `fac82e4ccff9c90ec6b068258e55e95689eb2a62`
- Integration branch versus current Runtime main:
  - behind = 0
- Public issues:
  - #11 ORCH-B01 remains OPEN only for live-provider completion evidence
  - #12 ORCH-B02 remains OPEN for full truncation-recovery flow

No Major-side Provider HTTP/SDK or retry workaround is allowed.

## 2. Gate interpretation

This version separates two states that were previously easy to conflate:

1. **Code / mechanism gate**
   - verifies architecture boundary, Runtime-owned provider execution, retry ownership,
     schema/error behavior, source/evidence mapping and Major domain contract.
2. **Live-provider acceptance**
   - verifies the same chain against the configured external provider and secret,
     then performs D01 real-business E2E.

The code/mechanism gate is now PASS.
The live-provider acceptance is still pending, therefore this document does **not**
claim real-model acceptance or product release.

## 3. G01–G07 result

| Gate | Result | Evidence / conclusion |
|---|---|---|
| G01 Baseline | PASS | integration contains accepted Major restore and Runtime main `f98a8922...`; compare to main behind=0 |
| G02 Contract | PASS | canonical D01 IDs, SourceBundle, deterministic request_id and event partition remain frozen |
| G03 Provider Boundary | **PASS (code)** | Runtime generic OpenAI-compatible Provider Adapter is in main and integration; no Major/Storage business HTTP handler required; one Runtime attempt = one actual request is acceptance-tested |
| G04 Retry Ownership | PASS | Runtime-only retry and hard provider-call budget validated |
| G05 Long Content | PASS | SourceRef / LogicalUnit / AtomicGroup / Coverage / business merge and business gate remain acceptance-tested |
| G06 Snapshot / Secret | PASS | configured provider secret is injected only at execution boundary and absent from persisted Snapshot/Attempt/Error/Result |
| G07 Acceptance | PASS | Runtime D8 + Major Domain Gate + Runtime D11 + Provider Boundary + REQ-022 all pass on current integration |

**Code Integration Gate: 7 / 7 PASS.**

## 4. Latest automated evidence

GitHub Actions:
- Run `35552572500`
- Result: **SUCCESS**

Acceptance:
- Runtime D8 major-issue fixture: **5 passed**
- Major-case Domain Integration Contract: **6 passed**
- Runtime retry / hard-budget: **20 passed**
- Runtime Provider Boundary: **5 passed**
- REQ-022 regression: **21 passed, 1 known deprecation warning**

Provider-boundary acceptance proves:
- Runtime auto-binds OpenAI-compatible provider execution from Agent Config;
- exactly one HTTP request per Runtime provider attempt;
- adapter / SDK hidden retry is disabled;
- 429 and validation retry are coordinated by Runtime;
- auth-less local OpenAI-compatible providers are supported;
- `finish_reason=length` maps to `OUTPUT_TRUNCATED / VALIDATION`;
- provider/model/provider_call_seq are observable on Attempt;
- provider secret does not persist in Runtime state.

## 5. D01 live-provider entry state

D01 real-provider work is now the next stage.

### Ready at code level
- Major Case domain adapter
- D01 canonical identifiers
- SourceBundle / event partition
- Runtime Provider Adapter
- Retry / hard budget
- Snapshot / Secret boundary
- Evidence and Business Gate contracts

### Still required before Issue #11 can close
`STORAGE-REAL-E2E-01` must execute against the configured live provider and prove:
1. real provider invocation succeeds through Runtime-owned execution;
2. actual request count equals Runtime provider_calls;
3. no hidden retry occurs;
4. output passes configured schema / Golden acceptance;
5. secret is absent from Runtime persistence and evidence/error surfaces.

Issue #11 remains OPEN until this evidence exists.

## 6. MAJOR-D01-E2E-001 acceptance

After ORCH-B01 live acceptance, execute:

```
Major Case / Document
        ↓
MajorCaseRuntimeDomainAdapter
        ↓
SourceBundle / Event Partition
        ↓
Unified Agent Runtime
        ↓
Runtime Provider Adapter
        ↓
Real Provider
        ↓
D01 structured output + Evidence
        ↓
Major business merge / Review Gate
```

Required evidence:
- real-provider Run / Attempt;
- provider_calls versus actual provider requests;
- no-secret persistence audit;
- D01 structured objects:
  - ISSUE_FACT
  - ROOT_CAUSE
  - ACTION
  - VERIFICATION
- Evidence traceability;
- Business Gate result.

## 7. ORCH-B02 relationship

ORCH-B01 now detects provider truncation and maps
`finish_reason=length → OUTPUT_TRUNCATED`.

This does **not** complete ORCH-B02.

During D01 real E2E, a truncation case must explicitly verify the full #12 chain:

```
OUTPUT_TRUNCATED
→ Runtime Long Content / Retry Strategy
→ chunk / AtomicGroup
→ multiple provider calls under budget
→ valid partial commit
→ resume if needed
→ Runtime merge mechanism
→ business merge/completeness gate
→ complete final result
```

If this chain is incomplete, #12 remains a P0 blocker for long-document production readiness.

## 8. Release boundary

Not yet verified:
- external real-provider acceptance;
- D01 real-business E2E;
- ORCH-B02 real truncation recovery;
- Repeat Case real-provider E2E;
- Windows BAT target-environment validation;
- manual de-identified business validation.

Formal Release remains **NOT APPROVED**.
