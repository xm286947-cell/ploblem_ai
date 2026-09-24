# Hardware Case Product Test V0.1

Status: **READY_FOR_INTERNAL_TEST**  
Release claim: **NO**  
Package: `HARDWARE_CASE_PRODUCT_TEST_V0.1_<commit>.zip`

This package is the formal internal test handoff for the currently implemented
Hardware Case MVP scope after P07 frontend completion.

## Test scope

Included and ready for internal test:

- P07 基础数据管理正式前端
- 电路/特性树、物料/器件树独立导入
- Excel 上传、Sheet/Header、动态 Mapping
- Tree Preview、Raw Preview、Validation
- ADD / UPDATE / RENAME / MOVE / DEPRECATE / NO_CHANGE / CONFLICT
- EXCLUDE as ChangeSet decision only
- Conflict blocking
- Atomic Apply
- SUCCESS / APPLIED_WITH_EXCLUSIONS / APPLY_FAILED
- Tree Version / import history
- Hardware Case backend/API publish-consume Golden Path
- DOCX parser + AI Adapter integration boundary
- company-only real validation harness
- synthetic package smoke

## Frozen red lines

1. DELETE is not a Tree Change Type.
2. ACTIVE formal nodes are never physically deleted by P07.
3. EXCLUDE is not a Tree Change Type and does not change formal node state.
4. APPLIED_WITH_EXCLUSIONS means pre-Apply exclusions + the remaining ChangeSet
   applied atomically and successfully.
5. APPLY_FAILED means the whole Apply failed; no new ACTIVE Version may be
   created and no partial-success message is allowed.
6. A missing node in a new Excel file does not imply DELETE or DEPRECATE.

## Known gaps

This test package does **not** claim the whole Hardware Case product is released.

- P01-P06 Hardware Case dedicated formal frontend is not part of this package.
- Real company Excel / Word / image samples are not bundled.
- Real Provider validation must run in the company-controlled environment.
- 20-30 real historical cases E2E is still pending.

## Security

Do not copy real company source materials into the package or GitHub.

The builder excludes:

- SQLite databases and logs
- .env
- local/secret YAML
- real business Word/Excel/images
- API keys / Authorization
- provider raw content / prompts from real runs

## Install

Recommended Python: 3.11.

```bash
python -m pip install -r requirements.txt -r requirements-runtime-p0-test.txt
```

## Start

Windows:

```bat
run_hardware_case_product_test.bat
```

Linux/macOS:

```bash
sh run_hardware_case_product_test.sh
```

P07:

`http://127.0.0.1:8080/p0/hardware-cases/base-data`

## Offline smoke

```bash
python scripts/hardware_case_product_test_smoke.py
```

Expected:

```text
RESULT=PASS
PRODUCT_TEST_PACKAGE=PASS
P07_FRONTEND=PASS
PACKAGE_STATUS=READY_FOR_INTERNAL_TEST
RELEASE_CLAIM=NO
```

## Company E2E input

Tester prepares locally:

- one real Circuit/Feature Excel
- one real Material/Device Excel
- approved real historical Hardware Case Word samples
- approved Unified Runtime / Real Provider local configuration

Never put these source files back into GitHub.

## Company E2E sequence

1. Start product.
2. Open P07.
3. Initial import Circuit Tree.
4. Initial import Material Tree.
5. Verify P02/P05 API consumers can use ACTIVE nodes.
6. Modify a copied local Excel and run UPDATE_IMPORT.
7. Verify Diff and Conflict behavior.
8. Verify EXCLUDE behavior.
9. Apply and verify new Tree Version.
10. Verify an omitted old node remains ACTIVE unless explicitly DEPRECATED.
11. Verify referenced node rename/move keeps stable node_id and historical
    tree_version/path_snapshot.
12. Run real Word -> Real AI -> Candidate -> Evidence -> human review -> mapping
    -> publish -> query/evidence.
13. Repeat on 20-30 real cases for MVP integration acceptance.

## Test callback

```text
RESULT = PASS / PARTIAL / BLOCKED
PACKAGE =
SOURCE_COMMIT =
ENVIRONMENT =
P07_TREE_IMPORT =
REAL_TREE_IMPORT =
REAL_AI =
REAL_CASE_E2E =
SECURITY_BLOCKERS =
DEFECTS =
KNOWN_ISSUES =
NEXT_GATE =
```

If P07 real-tree validation passes, request `HC_TREE_IMPORT_PRODUCT_GATE`.
The package remains a test package until the real-data integration gates pass.
