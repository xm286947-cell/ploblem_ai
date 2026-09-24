# Hardware Case Product Test FULL V0.1

Status: **READY_FOR_INTERNAL_TEST after CI Gate**  
Release claim: **NO**  
Package: `HARDWARE_CASE_PRODUCT_TEST_FULL_V0.1_<commit>.zip`

This is the first internal test package that contains the full Hardware Case
frontend P01-P07 plus the existing backend/API, Runtime/Agent and company-local
Evidence Source chain.

## Product entry

Start:

```bat
START_HARDWARE_CASE.bat
```

Default page:

`http://127.0.0.1:8080/p0/hardware-cases`

The product no longer starts on batch-analysis or P07.

## Frontend scope

- P01 案例首页
- P02 双树导航
- P03 案例搜索
- P04 案例详情
- P05 案例确认工作台
- P06 Evidence Viewer
- P07 基础数据管理

Hardware Case is also exposed as a primary entry in the existing professional
quality platform shell. No second Web/server/port is created.

## Consumer flow

```text
P01
 -> P02 tree navigation or P03 search
 -> P04 case detail
 -> P06 Evidence Viewer
 -> controlled source preview/file
```

Consumer pages default to PUBLISHED only. DEPRECATED is historical-only.

## Maintainer flow

Internal test maintainer view:

```text
http://127.0.0.1:8080/p0/hardware-cases?role=maintainer
```

Then:

```text
P05 review
 -> AI Candidate vs human Confirmed
 -> Circuit/Feature mapping
 -> Material/Device mapping
 -> Evidence
 -> Publish Gate
 -> PUBLISHED
```

P07 manages the two ACTIVE base trees through the frozen Excel Import workflow.

## First-time setup

1. Run:

```bat
INIT_LOCAL_CONFIG.bat
```

2. Edit:

`config\runtime\model.local.yaml`

3. Put company-local inputs only on the company machine:

```text
data\input\word\
data\tree\circuit_feature.xlsx
data\tree\material_device.xlsx
```

4. Configure the local validation file:

`config\hardware_case_real_validation.local.json`

5. Run:

```bat
CHECK_ENV.bat
```

Expected:

```text
RESULT=PASS
```

6. Start the product:

```bat
START_HARDWARE_CASE.bat
```

## Real AI

Agent:

`hardware_case.structure`

Run company-local Real AI validation:

```bat
RUN_REAL_AI_VALIDATION.bat
```

The chain is:

```text
Word
 -> DOCX parser
 -> Unified Runtime
 -> hardware_case.structure
 -> Candidate
 -> Evidence
 -> human review
 -> dual-tree mapping
 -> Publish Gate
```

## Evidence Source

P06 uses the company-local Source Registry. Absolute server paths are never
returned to the browser.

Supported MVP behavior:

- source metadata
- DOCX locator preview
- controlled original-file access
- SOURCE_UNAVAILABLE fail-closed
- HASH_MISMATCH fail-closed

Real source files are not bundled in the ZIP.

## Frozen product rules

- AI never auto-publishes.
- Candidate and Confirmed are visually and semantically separate.
- Consumer P01-P04 defaults to PUBLISHED.
- Circuit/Feature and Material/Device trees remain independent.
- One confirmed tree side can satisfy the minimum Mapping Gate.
- Both sides UNMAPPED cannot publish.
- Evidence is mandatory for publish.
- DEPRECATED does not enter default search/tree counts/recommendation.
- SOURCE_UNAVAILABLE keeps the Case but surfaces a warning.
- P07 does not support physical DELETE of formal tree nodes.
- EXCLUDE is not a Tree Change Type.
- Apply remains atomic.

## Internal test focus

Company E2E must validate:

1. P01 is the default product landing page.
2. both real Excel trees can be imported through P07;
3. P02 immediately consumes the ACTIVE trees;
4. real Word can run through Real AI;
5. P05 can review facts and confirm mappings;
6. Publish Gate blocks incomplete cases;
7. after publish, P01/P02/P03 can find the case;
8. P04 displays the complete engineering chain;
9. P06 can trace Evidence back to the controlled local source;
10. repeat the full path on 20-30 real historical cases.

## Open acceptance items

The package does **not** claim:

- REAL_DATA_VALIDATED
- AI_INTEGRATION_GATE_PASS
- 20_TO_30_REAL_CASE_MVP_INTEGRATION_GATE_PASS
- HC_TREE_IMPORT_PRODUCT_GATE
- MVP_INTEGRATION_GATE_PASS
- MVP_DEMO_GATE_PASS
- RELEASE_GATE_PASS
- Pilot Ready

## Security

Do not upload real Word/Excel/images, local source stores, runtime databases,
logs, model.local.yaml, API keys, Authorization or provider raw content back
to GitHub or public locations.

## Callback

```text
RESULT = PASS / PARTIAL / BLOCKED
PACKAGE =
SOURCE_COMMIT =
ENVIRONMENT =
P01_PRODUCT_ENTRY =
P02_TREE_NAV =
P03_SEARCH =
P04_DETAIL =
P05_REVIEW =
P06_EVIDENCE =
P07_TREE_IMPORT =
REAL_PROVIDER =
REAL_AI =
REAL_CASE_E2E =
SECURITY_BLOCKERS =
DEFECTS =
KNOWN_ISSUES =
NEXT_GATE =
```
