# Hardware R1 Native Patch Durability Acceptance (D2)

This gate runs the frozen D1 `hardware_patch_durability_gate.py` unchanged on native Windows and macOS GitHub Actions runners. Each runner gets the same two source-bound ZIP test packages, but creates its own installation and Persistent Data Root. The package is an application/source test ZIP with no installer; this gate does not certify an MSI, PKG, DMG, or formal release package.

## Frozen inputs

- OLD_SOURCE: `4a9cfdfc2c10366c86ab01efcc1c236da5942d38`
- NEW_SOURCE: `87fcfaede66f4565eb3fba12330f7b8fba51187a`
- PACKAGE_TYPE: ZIP
- INSTALLER_TYPE: NONE
- TEST_PACKAGE_NOT_RELEASE: YES

The package job uses `git archive` with an `application/` prefix, hashes each ZIP, writes a SHA-256 sidecar, and emits `package_manifest.json`. Native jobs verify the exact package IDs, source commits, sizes, and hashes before passing those same ZIPs to the D1 Harness. The full source-backed test ZIP intentionally contains the regression tests required by D1; the separately built product-test ZIP omits some of these test/runtime files and is not a substitute for this acceptance input.

## Native execution

`.github/workflows/hardware-r1-native-patch-durability.yml` builds the package pair once, then runs independent jobs on `windows-latest` and `macos-latest`. Each job installs the repository test requirements, verifies the package manifest, and runs the frozen D1 Harness with a fresh runner-temp data root and platform-specific evidence directory. The Harness builds S1–S7 using the existing Product/API/service flow, creates and verifies the pre-patch backup, replaces only the staged application root, performs two startups, compares durable identity, and runs F1/F2/F4/F5 plus existing F6–F8 regressions.

The summary step checks each platform's complete evidence set, package binding, zero critical diff, zero provider calls, independent installation IDs/data roots, backup/restore, startup idempotence, state coverage, and recovery fault results. It writes `native_gate_summary.json`; any missing report, failed assertion, or unsuccessful native job blocks the workflow.

## Evidence and certification boundary

Each runner uploads its D1 evidence under the artifact `hardware-native-durability-windows` or `hardware-native-durability-macos`. The summary is uploaded separately as `hardware-native-durability-summary` and includes package IDs and SHA-256 values for both platforms.

`INSTALLER_CERTIFICATION=NOT_RUN` and `FORMAL_RELEASE_PACKAGE_CERTIFICATION=NOT_RUN` remain explicit even when `NATIVE_PATCH_DURABILITY_GATE=PASS`. This D2 acceptance validates ZIP patch durability on native operating systems only.
