# P3 Release Delivery

## IMPLEMENTATION_STATUS

COMPLETED — P3 historical migration, HMI/PLC/IFA E2E acceptance and release regression are closed.

## MIGRATION_RESULT

The existing YAML-to-Mapping migration remains unchanged. P3 adds a release database migration gate covering Knowledge, Mapping versions and audit, configuration, AI/manual analysis definitions, values and audit history.

Workflow: `Dry Run → JSON Migration Report → Apply → Applied Report/Audit`.

Dry Run:

```bash
python main.py knowledge-release-migrate \
  --source /path/to/historical.db \
  --target /path/to/quality_issue_p3.db \
  --report /path/to/migration-report.json
```

Apply (only after reviewing a successful report):

```bash
python main.py knowledge-release-migrate \
  --report /path/to/migration-report.json \
  --apply
```

Safety controls: source is never modified; source SHA-256 is rechecked at Apply; occupied targets are rejected; SQLite integrity and foreign keys are checked before and after; current schemas are upgraded additively; the staging database is atomically promoted; the target stores `release_migration_audit`; an applied report is emitted.

Acceptance migration fixture preserved historical Knowledge versions and manual-analysis field definitions/values. It also verified rejection after source mutation, rejection for an occupied target, and Dry Run immutability.

## E2E_RESULT

HMI, PLC and IFA each passed an actual `.xlsx` request through P1/P2:

`Upload → Active Mapping → Preview → zero Knowledge writes → Confirm → Import Result → Knowledge write`.

Repeated Confirm was verified idempotent. Release acceptance also found and fixed the missing `load_workbook` import in the P1 preview route.

## MODIFIED_FILES

- `quality_knowledge/release_migration.py`
- `quality_knowledge/mapping/migration.py`
- `quality_knowledge/mapping/repository.py`
- `quality_knowledge/migration/__init__.py`
- `quality_knowledge/web/app.py`
- `main.py`
- `requirements.txt`
- `tests/test_p3_release_migration.py`
- `tests/test_p3_hmi_plc_ifa_e2e.py`
- `P3_RELEASE_DELIVERY.md`

## DATABASE/CONTRACT_CHANGES

- No Knowledge, Mapping or Human Analysis business contract changes.
- No breaking API or route changes.
- One additive audit table is created only in a successfully migrated P3 target: `release_migration_audit`.
- One additive CLI command: `knowledge-release-migrate`.
- Existing P1 Preview-before-Commit and P2 UED routes are reused unchanged.

## TEST_RESULT

- P3 + P1/P2 focused acceptance: `16 passed, 0 failed`.
- Full project regression: `239 passed, 0 failed`.
- Mapping duplicate-alias hotfix regression: full project `240 passed, 0 failed`.
- New-header recognition hotfix: explicit structural Preview, non-ID automatic detection and detected-row Preview covered; full project `243 passed, 0 failed`.
- Mapping table sticky-header/field-label hotfix: full project `244 passed, 0 failed`.
- Field difference workbench: Excel header comparison, similarity suggestions, Alias/Extension/Raw/Ignore actions, optional mapping disable and single-Draft generation; full project `247 passed, 0 failed`.
- Mapping Settings direct Excel comparison entry and shared difference workbench routing; full project `249 passed, 0 failed`.
- Explicit structural header detection accepts duplicate/new/single-column headers and title rows; full project `251 passed, 0 failed`.
- Multi-sheet selection now prefers the real data sheet over summary/title sheets and exposes selected/candidate sheets; full project `252 passed, 0 failed`.
- Excel structure reading now uses original cell content while import values prefer cached results with raw fallback; 51-column regression covered; full project `253 passed, 0 failed`.
- Data Intake structured diagnostics now record upload hash, workbook/sheet dimensions, header candidates, selected sheet, field counts and Preview result under `output/diagnostics/data_intake.log` without row data.
- Broken XLSX worksheet dimension metadata (for example declared `A1:A1` with actual `A1:AY34`) is forcibly recalculated in Preview and Import; full project `254 passed, 0 failed`.

## COMPATIBILITY_RESULT

- Existing databases are not modified in place.
- P2 behavior, A1–A7 workflows and existing YAML migration are retained.
- Existing Knowledge/Mapping/configuration/manual-analysis identifiers and history are preserved byte-for-byte before additive schema initialization.
- `.xlsx`/`.xlsm`, HMI/PLC/IFA and Preview-before-Commit remain compatible.
