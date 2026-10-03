# Storage #359 Real Golden Validation Status

TASK=STORAGE-SKILL-CONSUMPTION-REBASELINE-001  
ISSUE=#359  
PR=#361  
STATUS=CODE_READY / REAL_PROVIDER_BLOCKED_ENVIRONMENT

## 1. Real source truth verification

Frozen sources were read from the recovered authoritative Drive source bundle.

### G-TIMAR-97

Source:
- Drive id: `1vI0QRmrbooYAN0mFvZSvQA6_Idt-o4mP`
- source_id SHA256: `7242c29244f17e25ce31330dd6b97412a64e193c28e2e18599f759679dac5e1a`

Verified source facts:
- Capacity: 256GB / 512GB / 1TB / 2TB
- Interface: PCIe Gen 4, up to four lanes
- Protocol: NVMe Revision 2.0
- Host Memory Buffer: supported
- TBW: 768 / 1500 / 3000 / 6000 TB by capacity
- TBW condition: WAF=1
- Operating temperature:
  - A97M8 family: -40°C to +85°C
  - K97M8 family: -25°C to +85°C
  - S97M8 family: -10°C to +70°C
- PLP: Optional / part-number dependent
- Media wording: NAND Flash Memory
- The recovered datasheet excerpt does not justify inventing TLC/QLC.

SOURCE_TRUTH_TIMAR=PASS

### G-GD5F1GQ5

Source:
- Drive id: `1vjSqdrafib7z6Wl2bIKnQZ7lzRIHayKz`
- source_id SHA256: `99e7833a64c2bf9b85272218e66b911ca33fb3504e89af539104edf617cf6c1a`

Verified source facts:
- Pages per Block: 64
- Ambient operating temperature includes -40°C to +85°C / +105°C variants
- Status register C0H explicitly includes P_FAIL and E_FAIL
- P_FAIL is an explicit Program Fail indication
- E_FAIL is an explicit Erase Fail indication
- ECC status is separately documented
- Bad-block-management text and bad-block marks exist, but descriptive bad-block text alone is not a bad-block-count observability claim

SOURCE_TRUTH_GD=PASS
OBSERVABILITY_SOURCE_BOUNDARY=PASS

## 2. Deterministic / regression evidence

Latest successful code gates before the provider probe:
- Storage Four-Family Agent Routing Gate: PASS
- Storage Review UX: PASS
- Storage Skill Consumption Rebaseline focused gate: PASS
- #359 focused tests: 9 passed
- Existing Storage Domain Skill regression: 13 passed

The real-source excerpt fixture and opt-in provider test are now versioned:
- `tests/fixtures/storage_359_real_source_excerpts.json`
- `tests/test_storage_skill_consumption_real_provider_359.py`

The normal real-provider workflow now includes the #359 TIMAR/GD test:
- `.github/workflows/storage-real-provider-e2e.yml`

## 3. Real Provider probe result

Temporary evidence run:
- Workflow: Storage Four-Family Agent Routing Gate
- Run id: `37133513594`
- Source commit: `77838f27210696652b9089ff434422f0c48aa1f3`

Results before provider call:
- Four-family routing/config-source gate: PASS
- Historical four-family Golden asset gate: PASS
- Existing Storage product regression: PASS

Provider configuration gate:
- `DASHSCOPE_BASE_URL`: EMPTY
- `DASHSCOPE_API_KEY`: EMPTY
- Result: `BLOCKED_ENVIRONMENT`
- TIMAR/GD provider execution: SKIPPED

Therefore the following MUST NOT yet be claimed:
- `TIMAR_REAL_GOLDEN=PASS`
- `GD_REAL_GOLDEN=PASS`
- `DIRECT_FACT_FALSE_NEGATIVE=0`
- `DIRECT_FACT_FALSE_POSITIVE=0`

## 4. Re-run entry

After repository variable/secret configuration is available, dispatch:

`.github/workflows/storage-real-provider-e2e.yml`

against the #359 branch (or the merged source once approved).

Required configuration:
- Repository variable: `DASHSCOPE_BASE_URL`
- Repository secret: `DASHSCOPE_API_KEY`

The workflow will run:
1. existing Runtime real-provider smoke
2. #359 TIMAR 97 real-source Golden
3. #359 GD5F1GQ5 real-source Golden

## 5. Current gate

```text
HISTORICAL_ASSET_INVENTORY=PASS
SOURCE_TRUTH_TIMAR=PASS
SOURCE_TRUTH_GD=PASS
FOUR_DEVICE_PROFILE_BASELINE_RECOVERED=PASS
FOUR_DEVICE_GOLDEN_BASELINE_RECOVERED=PASS
NO_GOLDEN_REWRITE_TO_FIT_CURRENT_CODE=PASS
NO_PROFILE_REINVENTION=PASS

FOCUSED_SEMANTIC_GATE=PASS
SKILL_GATE_REGRESSION=PASS
REVIEW_UX_353_REGRESSION=PASS
R1_REGRESSION=PASS
VALID_EXISTING_CAPABILITY_LOST=0

REAL_PROVIDER_CONFIGURATION=BLOCKED
TIMAR_REAL_GOLDEN=NOT_RUN
GD_REAL_GOLDEN=NOT_RUN
DIRECT_FACT_FALSE_NEGATIVE=NOT_CLAIMED
DIRECT_FACT_FALSE_POSITIVE=NOT_CLAIMED

PRODUCT_GATE=NOT_CLAIMED
RC_PASS=NOT_CLAIMED
```
