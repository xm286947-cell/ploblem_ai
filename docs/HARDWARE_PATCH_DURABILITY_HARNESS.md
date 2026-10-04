# Hardware R1 Patch Durability Harness (D1)

The D1 gate creates durable state with the old application, gracefully stops it, replaces only a disposable application-root copy, and starts the new source/package against the same external Persistent Data Root. It does not certify a native Windows/macOS installer; that remains D2.

## Inputs

For each version, supply exactly one of `OLD_SOURCE` / `OLD_PACKAGE` and `NEW_SOURCE` / `NEW_PACKAGE`. A source tree is a repository directory containing `services/hardware_startup_coordinator.py`. A package is a ZIP or TAR archive with that application tree at its root or one directory below. Package extraction rejects path traversal, links, and device files.

The platform-neutral `--package-adapter module:function` hook accepts a future native-package adapter for Windows/macOS packaging. The adapter receives `(package_path, staging_directory)` and must return an application-tree path contained within that staging directory. This D1 change ships the hook and archive adapter only; it does not install or certify `.msi`, `.pkg`, `.dmg`, or other native packages. D2 must provide the appropriate adapter and run on the corresponding native platform.

`PERSISTENT_DATA_ROOT` must be fresh (absent or empty) and separate from application inputs and evidence. The Harness does not remove or reset an existing data root. Its sibling `user-config` directory holds the same bootstrap file across the simulated replacement. A second, isolated `*-restore-drill` root is created for backup restore verification; it must not already exist.

`EVIDENCE_ROOT` must be absent or empty, and separate from the application inputs. Existing evidence is never overwritten. The source inputs are copied read-only into a temporary application area; `.git`, build output (`dist/`), caches, virtual environments, and `node_modules` are excluded. The `package-closure` worktree is not an input unless explicitly supplied.

For a Git source, the gate records `HEAD` and requires a clean worktree. The old source must be the frozen D1 baseline `4a9cfdfc2c10366c86ab01efcc1c236da5942d38`. An archive without Git metadata uses `OLD_SOURCE_COMMIT` (defaulting to that baseline); set `NEW_SOURCE_COMMIT` when a package has no Git metadata.

## Run

```bash
python scripts/hardware_patch_durability_gate.py \
  --old-source /path/to/clean/old-source \
  --new-source /path/to/clean/new-source \
  --persistent-data-root /path/to/fresh/durable-test-data \
  --evidence-root /path/to/PATCH_DURABILITY_EVIDENCE
```

The same inputs can be provided as environment variables: `OLD_SOURCE`, `NEW_SOURCE`, `OLD_PACKAGE`, `NEW_PACKAGE`, `OLD_SOURCE_COMMIT`, `NEW_SOURCE_COMMIT`, `PERSISTENT_DATA_ROOT`, and `EVIDENCE_ROOT`; set `HARDWARE_PATCH_PACKAGE_ADAPTER` for the package adapter hook. One source/package input is required per version. The command runs focused pytest phases in separate processes so old and new imports cannot leak across the simulated patch.

## Created states and assertions

Synthetic DOCX payloads are uploaded once through the Workbench API. A deterministic Stage A/B adapter is injected only while constructing the pre-patch state and reports zero provider calls. Production Review, Promotion, publication, and verify use the existing Product APIs/services and the local Unified Knowledge public API adapter. Startup after replacement receives no extraction provider or Knowledge mutation adapter; the Harness does not replay Workbench, review, intake, publish, or verify operations.

The durable manifest checks Source identities/bytes, Candidate IDs and hashes, row versions and Review state, evidence IDs, Promotion ledger state, Formal references, operation journals, and Workbench Batch/Item bindings and run references. Preview/cache cleanup is followed by another durable-identity comparison. A separate isolated root exercises a published-backup restore without restoring over the upgraded root.

The gate also runs fault regressions F1, F2, F4, and F5; F3 is recorded as not applicable when schema versions do not change; the existing C2 Publication Recovery regression suite is rerun for F6-F8. A nonzero critical identity diff blocks the gate.

## Evidence

The gate writes the fixed evidence set under `EVIDENCE_ROOT`:

- `gate_manifest.json`
- `pre_patch_state.json`, `post_patch_state.json`, `identity_diff.json`
- `source_hash_report.json`, `candidate_hash_report.json`, `review_report.json`, `promotion_report.json`
- `backup_verify.json`, `restore_verify.json`, `startup_trace.json`, `provider_call_report.json`, `recovery_fault_report.json`
- `README.md`

A successful D1 result always records `native_patch_gate=NOT_RUN_D1_ONLY` and `READY_FOR_D2=NO`.
