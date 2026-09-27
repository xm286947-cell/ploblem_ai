# Knowledge Production Release Gate Asset Governance — #150

Status: **CLOSED**

Issue: `#150`

Canonical baseline: `main` at the governance-closure commit

Pinned Knowledge Release remains:

```text
KP-STORAGE-RC1-VALIDATION-001
contract_version=knowledge-query/v1
object_contract_version=knowledge-object/v1
candidate_contract_version=knowledge-candidate/v1
common_evidence_contract=common-evidence/v1.0
binding_contract=knowledge-release-binding/v1.0
```

## Asset audit

| Historical #150 asset | Decision | Current canonical asset / evidence | Reason |
|---|---|---|---|
| `.github/workflows/kp-storage-rc1-release-gate.yml` | `ABSORB` | `.github/workflows/kp-storage-rc1-release-gate.yml` on `main` | The workflow is now the canonical CI entry for the reproducible Knowledge Release delivery gate. Its trigger is canonical `main`/PR scope; it does not own product code or Storage tests. |
| `tools/kp_storage_rc1_release_gate.py` | `ABSORB` | `tools/kp_storage_rc1_release_gate.py` on `main` | The runner composes existing Knowledge Release, Query, Evidence/Source traceability, and Storage compatibility services. It adds no new Knowledge capability and no second release implementation. |

No #150 asset is `SUPERSEDE` or `RETAIN`-only. The historical delivery branch,
commit history, original workflow, and original runner remain available for
traceability; they are not deleted or rewritten.

## Current baseline comparison

The absorbed gate relies on the existing formal baseline:

```text
knowledge_production/release.py
knowledge_production/release_binding.py
knowledge_production/storage_compat.py
tests/test_kp_d03_review_publish.py
tests/test_kp_d04_release_query.py
tests/test_kp_d06_storage_golden.py
products/storage_rc1/knowledge_release/current/*
contracts/release_binding/v1/*
contracts/common_evidence/v1/*
```

The release version and current release snapshot are unchanged. The runner
replay on the current `main` API passed Knowledge Release construction, published
object query, Evidence traceability, Source Reference traceability, and Storage
consumer compatibility. Storage product regression and Product Test Center are
out of scope for this governance closure.

## Closure result

```text
ORPHAN_RELEASE_GATE_ASSET=0
AMBIGUOUS_ACTIVE_BASELINE=0
DUPLICATE_ACTIVE_GATE=0
CURRENT_KNOWLEDGE_RELEASE_UNCHANGED=YES
STORAGE_REGRESSION_REQUIRED=NO
PRODUCT_RETEST_REQUIRED=NO
VALID_FIX_LOST=0
ISSUE_150=CLOSED
```
