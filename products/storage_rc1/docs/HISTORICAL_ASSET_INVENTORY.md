# Storage Historical Asset Inventory

TASK=STORAGE-SKILL-CONSUMPTION-REBASELINE-001  
ISSUE=#359  
WAVE=W0 Historical Asset Recovery / Reuse Gate  
BASELINE=integration/storage@b0fa15633033ca2c8083b5df35568759082a7b61  
STATUS=RECOVERY_BASELINE_V0.1

## 1. Governing Rules

- DO_NOT_REINVENT_PREVIOUSLY_FROZEN_PROFILE_OR_GOLDEN_ASSETS
- NO_GOLDEN_REWRITE_TO_FIT_CURRENT_CODE
- No extraction / Skill / Knowledge behavior change is authorized by this inventory alone.
- If current behavior conflicts with a frozen historical asset, classify first as:
  - CURRENT_REGRESSION
  - INTENTIONAL_VERSION_CHANGE
  - HISTORICAL_ASSET_DRIFT
- Frozen historical Golden/Profile assets are evidence to compare against, not material to rewrite for a green test.

## 2. Recovered Historical Source Bundle

Authoritative recovered Drive bundle:

- Folder: STORAGE RC1 R5 historical UAT evidence/source bundle
- Drive folder id: `1Lk0_HE7uw7M43q7EGewQk1apGWhielrv`

Recovered real-device source materials:

| Device type | Golden sample | Historical source | Drive id | SHA256 / source_id | Reuse |
|---|---|---|---|---|---|
| eMMC | SkyHigh S40FC016 | `STDL-PM-001_skyhigh_s40fc016.pdf` | `1jObAf9vbwsHzu9X-sLM5kW9Gz4L8jjId` | `a40b2ecc44c89184dbcaed3f8d48e7615e4daefaf7732076a40f7bc73751e978` | DIRECT_REUSE |
| NAND Flash | GigaDevice GD5F1GQ5UExxG | `STDL-PM-009_gd5f1gq5.pdf` | `1vjSqdrafib7z6Wl2bIKnQZ7lzRIHayKz` | `99e7833a64c2bf9b85272218e66b911ca33fb3504e89af539104edf617cf6c1a` | DIRECT_REUSE |
| NOR Flash | GigaDevice GD25Q64E | `STDL-PM-008_gd25q64e.pdf` | `1IEGloojrNGb247kqPri86KZt1VKUk1B2` | `1330342a7dd6d123bde0486fb2381a359f6a8890a647d4f7d2a282a963938602` | DIRECT_REUSE |
| SSD | TIMAR 97 Series | `STDL-PM-010_timar_97_series.pdf` | `1vI0QRmrbooYAN0mFvZSvQA6_Idt-o4mP` | `7242c29244f17e25ce31330dd6b97412a64e193c28e2e18599f759679dac5e1a` | DIRECT_REUSE |
| SSD | TIMAR K97M8-Y product-page snapshot | `STDL-PM-011_timar_k97m8.html` | `19lCC_0eVfGDQ4quwYRl0xPpszrxB6d6l` | `dbc95e45dfcea8694f17d2e1951d7a95647a139d0ed104a4bde5edc10e2ba29c` | DIRECT_REUSE |

Additional recovered bundle assets:

- `STDL-PM-007_PM_NAND_baseline.md` — NAND method/Golden baseline copy
- `STDL-PM-002..006_PM_NATIVE_*_text_snapshot.txt` — historical frozen text snapshots
- Copy of eMMC Schema & GoldenSample V0.2
- Copy of NOR Schema & GoldenSample V0.2
- Copy of SSD Schema & GoldenSample V0.2
- Copy of NAND ChatGPT single-pass Benchmark V0.1
- Copy of `STORAGE_RC1_R5_UAT_DATA_EVIDENCE_PACK_V0.1`

Source byte hashes above were computed from the recovered original Drive bytes and are frozen as the canonical `source_id=SHA256` candidates for W0 reuse. They MUST NOT be replaced by hashes of filenames, Drive ids, extracted text, or regenerated copies.

## 3. Recovered Frozen Profile / Schema / Golden Baselines

### 3.1 NAND — GD5F1GQ5UExxG

Historical baseline:
- `存储器件寿命知识库_方法演进与NAND验证基线_V0.1_20260918.md`
- Drive id: `1G4PI2XlUntVTqtLmQ3ClMcccIUqK6ov6`
- Supporting schema: `NAND_Flash_Parameter_Schema_V0.1.md`
- Drive id: `1qMaezV6Uhbhs1VhOdKwBCxf5_nPPMG6l`

Frozen sample identity:
- Manufacturer: GigaDevice
- Product: GD5F1GQ5UExxG / GD5F1GQ5xExxG family
- Document: DS-00888
- Revision: Rev1.5
- Revision date: 2023-03-10
- Type: 1Gb SLC NAND Flash

Frozen semantics include:
- `field_key/value/unit/condition/scope_type/scope_values/evidence/confidence/status/derived`
- status: `found/missing/ambiguous/conflict`
- P/E: 100,000 cycles
- Endurance condition: With ECC
- Retention: 10 years
- Page size: 2048 B
- Spare/OOB: 128 B subject to ECC mode
- Pages per block: 64
- Block count: 1024/die
- Internal ECC + ECC capability/status semantics
- P_FAIL and E_FAIL are explicit diagnostic capabilities
- bad-block facts and runtime observability are not to be collapsed

Current repository equivalents:
- `products/storage_rc1/config/spec_templates.yaml`
- `products/storage_rc1/storage_life/parameter_baseline.py`

Reuse status:
- Schema/Profile semantics: MIGRATE
- Real-device Golden/source: DIRECT_REUSE
- R6 synthetic fixture `M24_PARAMETER_GOLDEN_RAW_NAND.json`: TEST_ONLY

Preliminary drift watch:
- Synthetic M24 uses generic `bad_block_observability=MTD/UBI statistics`; this MUST NOT override the real GD5F Golden rule that observability requires explicit field/register/command/API/counter/acquisition evidence.
- Any current inference of bad-block count observability from descriptive bad-block text is a candidate CURRENT_REGRESSION.

### 3.2 NOR — GD25Q64E

Historical baseline:
- `存储器件寿命知识库_NOR_Schema与GoldenSample基线_V0.2_20260918`
- Drive id: `1mO1i5DrGrxiwT8_ZIbU3KP6eT864fUXBd7JLCzgbFsY`

Recovered repository evidence:
- `products/storage_rc1/tests/fixtures/gd25q64e_rev16_excerpt.json`
- `products/storage_rc1/tests/test_gd25q64e_acceptance_next001.py`
- `products/storage_rc1/tests/test_gd25q64e_golden_next001.py`

Historical sample identity:
- GigaDevice GD25Q64E
- DS-00484
- Rev1.6
- 2024-04-28

Frozen representative facts:
- minimum 100,000 Program/Erase cycles
- 20-year data retention typical
- program page size 256 B
- sector erase 4 KB
- block erase 32 KB / 64 KB
- program/erase timing with condition
- status register
- WIP/WEL/Suspend
- Program Fail / Erase Fail / Error Flag / ECC Status may legitimately be missing and MUST NOT be fabricated

Current repository equivalents:
- `products/storage_rc1/config/spec_templates.yaml`
- `products/storage_rc1/storage_life/parameter_baseline.py`

Reuse status:
- Schema/Golden V0.2: DIRECT_REUSE
- Real source + excerpt fixture: DIRECT_REUSE
- R6 synthetic `M25_PARAMETER_GOLDEN_NOR.json`: TEST_ONLY

### 3.3 eMMC — SkyHigh S40FC016

Historical baseline:
- `存储器件寿命知识库_eMMC_Schema与GoldenSample基线_V0.2_20260918`
- Drive id: `12IeMoitFAaRfH8-bI1DHAhtISIw68sfuhXzWm04-jXk`

Frozen sample identity:
- SkyHigh Memory S40FC016
- 16 GB e.MMC
- document 002-01118 Rev D
- 2025-04-29

Frozen contract includes:
- common fact fields plus `knowledge_type`
- `specification / diagnostic_capability / device_requirement`
- native NAND type separated from partition storage mode
- Enhanced/SLC mode MUST NOT imply whole-device SLC
- partition facts preserve scope
- DEVICE_LIFE_TIME_EST_TYP_A -> EXT_CSD[268]
- DEVICE_LIFE_TIME_EST_TYP_B -> EXT_CSD[269]
- PRE_EOL_INFO -> EXT_CSD[267]
- BKOPS_STATUS -> EXT_CSD[246]
- vendor health access may be vendor-specific and must remain evidence-backed

Current repository equivalents:
- `products/storage_rc1/config/spec_templates.yaml`
- `products/storage_rc1/storage_life/parameter_baseline.py`

Reuse status:
- Schema/Golden V0.2: DIRECT_REUSE
- real source PDF: DIRECT_REUSE
- R6 synthetic `M03_PARAMETER_GOLDEN_EMMC.json`: TEST_ONLY

### 3.4 SSD — TIMAR 97 Series / K97M8-Y

Historical baseline:
- `存储器件寿命知识库_SSD_Schema与GoldenSample基线_V0.2_20260918`
- Drive id: `1pU3Kx7fCqwhgFtJOo32mqL6ydBYpqJL0vMGttLal7xM`

Recovered frozen sources:
- 97 Series datasheet PDF: `1vI0QRmrbooYAN0mFvZSvQA6_Idt-o4mP`
- K97M8-Y product-page snapshot: `19lCC_0eVfGDQ4quwYRl0xPpszrxB6d6l`

Frozen comparison/UAT examples include:
- K97M8-Y 256GB: TBW 768 TB, condition WAF=1
- K97M8-Y 1TB: TBW 3000 TB, condition WAF=1
- capacity-scoped TBW MUST NOT be promoted to the whole 97 Series
- facts must retain Evidence and scope

#359 additionally freezes direct-fact expectations for TIMAR:
- capacities: 256GB / 512GB / 1TB / 2TB
- PCIe Gen4x4 / PCIe 4.0 up to 4 lanes
- NVMe Revision 2.0
- TBW 768/1500/3000/6000 TB by capacity
- TBW condition WAF=1
- family-specific operating-temperature ranges
- PLP optional / part-number dependent
- HMB supported
- NAND media only as `NAND Flash` unless cell type is explicitly stated

Current repository equivalents:
- `products/storage_rc1/config/spec_templates.yaml`
- `products/storage_rc1/storage_life/parameter_baseline.py`

Reuse status:
- Schema/Golden V0.2: DIRECT_REUSE
- real source PDF + product snapshot: DIRECT_REUSE
- R6 synthetic `M23_PARAMETER_GOLDEN_SSD_NVME.json`: TEST_ONLY

Preliminary drift watch:
- current product extraction that returns interface/protocol unchecked or operating temperature N/A for this Golden is a candidate CURRENT_REGRESSION, not a reason to rewrite the Golden.

## 4. Four-Device Frozen Regression Fixtures

Historical R6 baseline:
- branch: `baseline/storage-r6-canonical-r1-20260925`
- commit: `4f320965b2c1acb1b9043ae38b4157d6e2cfecf1`

Recovered fixtures:
- `test_assets/storage_rc1/fixtures/M03_PARAMETER_GOLDEN_EMMC.json`
- `test_assets/storage_rc1/fixtures/M23_PARAMETER_GOLDEN_SSD_NVME.json`
- `test_assets/storage_rc1/fixtures/M24_PARAMETER_GOLDEN_RAW_NAND.json`
- `test_assets/storage_rc1/fixtures/M25_PARAMETER_GOLDEN_NOR.json`

Classification:
- These are frozen regression assets and MUST be retained.
- They are synthetic/mocked parameter-contract fixtures, not substitutes for the real-device Golden samples above.
- Reuse status: TEST_ONLY.

## 5. Device Profile Registry Recovery

No authoritative historical `profiles/nand.json|nor.json|emmc.json|ssd.json` path has been recovered in Git history searched so far.

Canonical migrated equivalent is already present in current code:
- `products/storage_rc1/config/spec_templates.yaml`

It contains:
- device family aliases
- family field sets
- analysis fields
- generic section groups
- vendor registry/aliases
- supported device types
- vendor-specific heading/section overrides
- GigaDevice NAND mappings
- SkyHigh eMMC mappings
- TIMAR eMMC/SSD mappings

Additional product-level canonical field baseline:
- `products/storage_rc1/storage_life/parameter_baseline.py`

Classification:
- historical literal `profiles/*.json`: MISSING_PATH / DO_NOT_RECREATE_BLINDLY
- canonical migrated profile semantics: DIRECT_REUSE
- consolidation into one explicit registry manifest, if needed by #359: MIGRATE metadata only, no semantic reinvention.

## 6. Golden / Benchmark Registry Recovery

Recovered sources are currently distributed rather than represented by one authoritative registry file:

- Drive real-device Golden/Schema baselines
- R6 mock fixtures M03/M23/M24/M25
- GD25Q64E repository fixture + tests
- `products/storage_rc1/skills/golden_cases.yaml`
- `products/storage_rc1/skills/material_inventory.yaml`
- historical NAND Benchmark V0.1

Classification:
- source assets: DIRECT_REUSE
- one explicit index/manifest: MIGRATE
- rewriting historical expectations: FORBIDDEN

## 7. Source / Evidence / Scope / Condition / Conflict Contract Recovery

Recovered historical Schema contracts explicitly preserve:
- `source/evidence`
- `condition`
- `scope_type`
- `scope_values`
- `status`
- `conflict`
- `ambiguous`
- `derived`
- `knowledge_type` where applicable

Current #353 field-level audit and Evidence trace remain non-regression constraints.

Recovery status:
- Evidence semantics: RECOVERED
- scope semantics: RECOVERED
- condition semantics: RECOVERED
- conflict/ambiguity semantics: RECOVERED
- original source byte SHA256/source_id binding: RECOVERED for the four Golden sources plus the TIMAR K97M8-Y product-page snapshot

## 8. Preliminary Reuse Matrix

| Asset | Current equivalent | Status | Drift action |
|---|---|---|---|
| NAND Profile/Schema | spec_templates.yaml + parameter_baseline.py | MIGRATE | compare field semantics; no reinvention |
| NAND GD5F Golden | Drive baseline + PDF | DIRECT_REUSE | regression oracle |
| NOR Profile/Schema | spec_templates.yaml + parameter_baseline.py | MIGRATE | compare field semantics |
| NOR GD25 Golden | Drive V0.2 + repo fixture/tests + PDF | DIRECT_REUSE | regression oracle |
| eMMC Profile/Schema | spec_templates.yaml + parameter_baseline.py | MIGRATE | preserve scope and health semantics |
| eMMC S40FC016 Golden | Drive V0.2 + PDF | DIRECT_REUSE | regression oracle |
| SSD Profile/Schema | spec_templates.yaml + parameter_baseline.py | MIGRATE | preserve capacity-scoped TBW/conditions |
| SSD TIMAR Golden | Drive V0.2 + PDF + HTML snapshot | DIRECT_REUSE | regression oracle |
| M03/M23/M24/M25 fixtures | same repo paths | TEST_ONLY | no promotion to real Golden |
| Device Profile Registry | spec_templates.yaml vendor/device registry | DIRECT_REUSE | add index only if needed |
| Golden/Benchmark Registry | distributed assets | MIGRATE | build index, do not rewrite assets |
| source_id SHA256 bindings | original recovered Drive bytes | DIRECT_REUSE | frozen in this inventory / registry |
| Runtime Observation | separate live DUT evidence | MISSING for W0 source bundle | never fabricate |

## 9. W0 Gate Status

```text
HISTORICAL_ASSET_INVENTORY=PASS_FOR_RECOVERED_BASELINE
FOUR_DEVICE_PROFILE_BASELINE_RECOVERED=PASS_WITH_MIGRATED_EQUIVALENT
FOUR_DEVICE_GOLDEN_BASELINE_RECOVERED=PASS
SOURCE_BUNDLE_BASELINE_RECOVERED=PASS
EVIDENCE_SCOPE_CONDITION_CONFLICT_SEMANTICS=PASS
DEVICE_PROFILE_REGISTRY_SEMANTICS=PASS_WITH_MIGRATED_EQUIVALENT
GOLDEN_BENCHMARK_REGISTRY=PASS_WITH_CANONICAL_INDEX
SOURCE_ID_SHA256_BINDING=PASS
NO_GOLDEN_REWRITE_TO_FIT_CURRENT_CODE=PASS
NO_PROFILE_REINVENTION=PASS
```

## 10. Next Authorized Step

Before product-code changes:
1. bind the four real Golden samples to the machine-readable regression manifest without changing their expectations;
2. execute current implementation against those frozen expectations;
3. classify each delta as CURRENT_REGRESSION / INTENTIONAL_VERSION_CHANGE / HISTORICAL_ASSET_DRIFT;
4. only CURRENT_REGRESSION items may proceed to minimum code repair under #359.

No Skill rewrite, second Knowledge stack, second Runtime, or Golden rewrite is authorized.
