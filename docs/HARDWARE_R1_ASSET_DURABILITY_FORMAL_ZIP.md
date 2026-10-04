# Hardware R1 Asset Durability Formal ZIP

## Certification boundary

This package certifies **Hardware R1 Asset Durability only**. It is not a
certification or release declaration for the entire Hardware Case MVP.

In scope: Source Store, Persistent Data Root, Durable Candidate Asset,
Production Review, Promotion Asset Ledger, Publication Recovery,
backup/restore, startup/recovery, Windows and macOS fresh-extract native
startup, the frozen D1/D2 patch-durability evidence, and preservation of
source/candidate/review/promotion/formal-reference identities.

Out of scope: the entire Hardware Case MVP release, Tree product release
gates, the 20–30 real-case integration gate, the full frontend product gate,
and MSI/DMG/PKG installer certification. This workflow does not claim formal
Hardware product release certification.

The generated package is named
`HARDWARE_R1_ASSET_DURABILITY_FORMAL_<source-short>.zip`, where `<source-short>`
is the first 12 characters of the exact 40-character source commit. A manual
workflow run accepts both values and verifies that the package identity is
derived from the full source SHA. It is built afresh from the frozen runtime
allowlist. Neither the existing
`HARDWARE_CASE_PRODUCT_TEST_FULL_V0.1` ZIP nor the
`HARDWARE_CASE_MVP_RC0_PREP` archive is copied, relabeled, or reused as the
certified package; their existing statuses remain unchanged.

## Evidence binding

The workflow accepts only frozen D2 run `37194611177` at head
`ce04ec824e1682891e6266e628e48ecbc4995c2e`, with the four pinned, unexpired
artifact IDs. It verifies the Windows and macOS S1–S7 state coverage, zero
critical identity diff, zero provider calls during patch, backup/restore, and
fault-smoke reports. The package builder also reconstructs the runtime
payload from D2's tested source and requires exact inventory and SHA-256
equality with the runtime payload built from the release source commit.

The Windows and macOS jobs independently verify the ZIP SHA-256, safely
extract it into a fresh directory, install the packaged runtime dependencies,
launch the packaged entrypoint with an isolated persistent data root, and
check `/health`, `/api/system/hardware/startup`, and `/p0/hardware-cases`.
The `/ready` response is recorded for diagnostics only; it is a product-wide
dependency gate (including Unified Knowledge availability), so it is not an
Asset Durability certification criterion. Product readiness remains
`NOT_CERTIFIED_OUT_OF_SCOPE`. The detached `RELEASE_MANIFEST.json` binds the exact ZIP
SHA-256, source commit, D2 artifacts, and both native reports.

The ZIP's `RELEASE_CONTENT_MANIFEST.json` remains pending until native startup
reports are bound:

```ini
CERTIFICATION_STATUS=PENDING_NATIVE_STARTUP_BINDING
CERTIFICATION_SCOPE=HARDWARE_R1_ASSET_DURABILITY
```

Only the detached `RELEASE_MANIFEST.json`, emitted after both native startup
reports pass, records:

```ini
CERTIFICATION_STATUS=PASS
CERTIFICATION_SCOPE=HARDWARE_R1_ASSET_DURABILITY
HARDWARE_CASE_MVP_RELEASE=NOT_CERTIFIED
PRODUCT_RELEASE_CLAIM=NOT_MADE
INSTALLER_CERTIFICATION=NOT_RUN
TEST_PACKAGE_NOT_RELEASE=NOT_REUSED_AS_RELEASE
```

`CERTIFICATION_STATUS=PASS` means only that this scoped Asset Durability gate
passed. It must not be interpreted as Hardware Case MVP or product-wide release
certification. The native workflow certifies fresh-extract startup; patch
durability is bound from the frozen D2 native gate and is not rerun in this
workflow.
