# Storage Domain Skills — Real Knowledge Gap Report V0.1

Baseline: `integration/storage@8a82d3b6825b86dc22a64a0787722ada73b04dd0`

Formal Knowledge Release: `KP-STORAGE-RC1-VALIDATION-001`

## Executive status

The current Formal Knowledge Release contains **1 active Knowledge Object**, **1 bound Evidence**, and **1 SourceReference**.

The released object is:

- `Percentage Used` / object `KO-4ad47002b1ba4070fa499559`
- source `NVME@2.0d`
- publisher: NVM Express
- title: NVM Express Base Specification
- version: 2.0d
- official URL: https://nvmexpress.org/specification
- archive ref: `seed/03_SSD_NVME/NVM-Express-Base-2.0d.pdf`
- evidence: `EVD-98019c742313d72837696c0b`
- evidence locator: page 200 / SMART / Health
- evidence status: BOUND

This object is sufficient to support only a narrow part of NVMe health/lifetime semantics. It is **not sufficient** to declare all 4 Knowledge Packs ready.

## Pack readiness

| Pack | Status | Current formal objects | Main gap |
| --- | --- | ---: | --- |
| PACK_WRITE_GOVERNANCE | BLOCKED | 0 | No released Linux writeback/fsync/ext4/WAL/small-write governance knowledge |
| PACK_LIFETIME_ENGINEERING | PARTIAL | 1 | Only NVMe Percentage Used is released; TBW/DWPD/DUW, NAND/eMMC endurance knowledge remains missing |
| PACK_DIAGNOSTIC_VALIDATION | PARTIAL | 1 | NVMe Percentage Used trace is available; eMMC Life Time/PRE_EOL and NAND diagnostics are not released |
| PACK_CHANGE_IMPACT | PARTIAL | 1 | One NVMe health semantic can support a narrow monitoring delta only; full parameter→software/test impact knowledge is absent |

## Source verification boundary

Two different states must not be conflated:

1. **Formal Release trace completeness:** the one released NVMe object has complete Object → Evidence → SourceReference → URL/archive/version/location trace.
2. **External Catalog verification:** the source catalog still records the broader EK-001..EK-028 source set as pending title/version verification. Catalog placeholders are not counted as Formal Knowledge.

Therefore an answer may cite the released NVMe object as formal evidence, but may not promote pending Catalog entries into formal facts.

## Real Golden status

### RG01 — Software Write Governance
Status: **PARTIAL**

Current release does not contain formal write-governance objects for high-frequency small writes, cache/coalescing, ext4 journaling, Linux writeback, SQLite WAL, or fsync boundaries.

Required next owner: Knowledge Production / Source Verification.

### RG02 — SSD/NVMe Lifetime Engineering
Status: **PARTIAL**

Supported now:
- NVMe Percentage Used formal semantics with evidence drill-down.

Still missing:
- formal TBW/DWPD/Data Units Written knowledge,
- real confirmed endurance/device facts for a specific DUT,
- runtime write observations where applicable,
- registered target-service-life write-budget formula.

`FORMULA_GAP=YES`
`FORMULA_TASK_REQUIRED=YES`

The Skill must not invent a daily write budget or remaining years.

### RG03 — eMMC Diagnostic
Status: **PARTIAL**

No released formal object currently supports Life Time A/B or PRE_EOL semantics. The Skill must fail closed rather than infer protocol tiers from memory or Catalog placeholders.

Required next owner: Knowledge Production / Source Verification.

### RG04 — Raw NAND Diagnostic / Lifetime
Status: **PARTIAL**

No released formal object currently supports P/E, erase-block, erase-count, wear-distribution, ECC, or bad-block interpretation.

Required next owner: Knowledge Production / Source Verification.

### RG05 — Change Impact
Status: **PARTIAL**

A narrow NVMe Percentage Used monitoring semantic is available, but the release does not contain enough cross-domain formal knowledge for the complete parameter delta → lifetime → software → monitoring → validation chain. Two real or product-approved device profiles are also required.

`UNKNOWN != SAFE`
No automatic replacement approval/rejection is permitted.

## Priority knowledge-production backlog

1. NAND / Raw Flash: EK-001, EK-002, EK-003, EK-005, EK-011, EK-012, EK-013
2. eMMC standard health: EK-007, EK-025
3. NVMe SMART / diagnostics: EK-018, EK-019
4. Linux write path: EK-004, EK-020, EK-021, EK-022, EK-028
5. SSD endurance/workload: EK-015, EK-016, EK-017, EK-026, EK-027

These records must pass source title/version verification and Knowledge Production review/publish before being counted as Formal Knowledge.

## Product/API surface

The existing Storage application exposes:

- `GET /api/product/skills/readiness`
- `GET /api/product/skills/real-golden`
- `POST /api/product/skills/{skill_id}/execute`

No second UI, Runtime, Provider, Knowledge Store, or Knowledge Production database access is introduced.
