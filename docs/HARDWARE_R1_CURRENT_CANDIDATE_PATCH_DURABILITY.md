# Hardware R1 Current Candidate Patch Durability

This task reuses the already-passed D1 Patch Durability Harness and D2 native wrapper. It does not define new product behavior.

## Binding

- OLD baseline: `4a9cfdfc2c10366c86ab01efcc1c236da5942d38` (the frozen D1 durable-state baseline; changing it would change D1 Expected rather than rebind D2)
- NEW: exact GitHub Actions checked-out candidate SHA (`github.sha`); for pull requests this is the source-bound synthetic merge commit containing the latest stacked base plus this PR
- Platforms: Windows + macOS
- Package type: exact-source ZIP
- Installer certification: NOT_RUN
- Formal release certification: NOT_RUN
- Real Word / Real Provider: NOT_RUN

The existing PR #386 frozen default binding remains the default of `hardware_native_patch_durability.py`. This task only adds explicit source parameters so a newer candidate can reuse the same verifier.

## Required gate

The workflow must retain the D1 fail-closed assertions:

- S1–S7 durable states
- critical identity diff = 0
- Source / Candidate / Review / Promotion / Formal Reference / Batch history preserved
- no empty DB fallback
- startup/migration/patch Provider calls = 0
- verified pre-patch backup
- isolated restore drill
- fault smoke and C2 recovery regression

A PASS is evidence only for patch durability of the bound source ZIPs. It is not real-corpus acceptance, installer certification, or product release certification.


## Baseline rule

An incremental attempt using `87fcfaede...` as OLD was rejected by the frozen D1 Harness with `OLD_SOURCE_BASE_MISMATCH`. That is expected fail-closed behavior, not a product failure. Current-candidate validation therefore preserves the frozen D1 OLD baseline and changes only NEW to the exact source-bound current candidate. This tests cumulative upgrade durability without modifying D1 semantics.
