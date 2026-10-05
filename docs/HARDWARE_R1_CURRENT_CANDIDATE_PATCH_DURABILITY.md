# Hardware R1 Current Candidate Patch Durability

This task reuses the already-passed D1 Patch Durability Harness and D2 native wrapper. It does not define new product behavior.

## Binding

- OLD baseline: `87fcfaede66f4565eb3fba12330f7b8fba51187a`
- NEW: exact pull-request head SHA supplied by GitHub Actions
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
