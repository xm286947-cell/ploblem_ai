# Quality Scenario Windows Compatibility Acceptance

## Purpose

This package is a Windows-first compatibility test candidate for the mature Quality Scenario product flow.

It is **not** the final production release and it is **not** the real-provider semantic acceptance gate.

The Windows package validates that the mature host, browser flow, Candidate lifecycle, traceability UI, idempotency and source-revision mechanics behave correctly on Windows using controlled test-only source data and the repository OpenAI-compatible mock provider.

## One-click startup

Double-click:

`start_quality_scenario_windows_acceptance.bat`

The launcher will:

1. create a package-local `.venv` when needed;
2. install declared dependencies when missing;
3. create fresh controlled G1-G5 source-side test data;
4. keep Source DB and QSV1 lifecycle DB separated;
5. start the repository controlled OpenAI-compatible provider;
6. start the mature `knowledge-web` host;
7. open:

`http://127.0.0.1:18080/software-assessment#quality-scenario-production`

No real Provider credential is required for this Windows compatibility gate.

## Using your existing mature DB on Windows

No script editing is needed:

1. Copy the original DB to the extracted package root.
2. Name the copied file `quality_issue_v1.db`.
3. Double-click `start_quality_scenario_windows_original_db.bat`.

Alternatively, drag the original DB onto that BAT file.

The runner makes its own further internal copy under `validation/windows_acceptance/original_quality_db_copy.db`.
The input DB is not changed. Source/material/assessment facts come from the copied DB.
QSV1 lifecycle writes use a dedicated Windows test DB; existing QSV1 published history from the input DB is **not** imported into that test DB.

Original-DB mode does **not** inject any synthetic G1-G5 records. It tests startup, mature navigation, and the source records actually present in your DB.

To run the deterministic G1-G5 controlled test cases, instead double-click `start_quality_scenario_windows_acceptance.bat` with no arguments. The G5 revision helper applies to that controlled-fixture mode only.

## Startup troubleshooting

Startup progress prints every 10 seconds; original-DB mode waits up to 300 seconds.
A Python service crash is detected without waiting for the full timeout.

On failure, the window prints `STARTUP_ERROR`, `APP_PROCESS_EXIT`, and the last 160 lines between
`APP_LOG_TAIL_BEGIN` / `APP_LOG_TAIL_END`.

Full service output stays at `validation/windows_acceptance/mature_app.log`. This is the diagnostic file to inspect if your specific original DB still fails.
No real-provider credential is required for this package.

## Acceptance checklist

### W1 - Startup and mature navigation

Expected:

- `/issues` opens.
- `/software-assessment` opens.
- `/quality-scenarios/workbench` opens.
- `/quality-scenarios/library` opens.
- There is no requirement to enter through a P0 root.

### W2 - G1 complete source flow

Select the G1 source case and click **生成质量场景** directly. There is no separate completeness-preview operation; the service checks sources automatically.

Expected:

- source binding and completeness checks happen automatically;
- Candidate is created;
- workbench can review / confirm;
- publish completes;
- published scenario appears in mature library/detail;
- traceability page remains accessible.

This gate validates Windows mechanics only; the controlled provider output is deterministic and must not be used as product-semantic evidence.

### W3 - G2 missing missed-test source

Select the G2 source case.

Expected:

- MISSED_TEST remains MISSING in source completeness;
- generation is not blocked merely because MISSED_TEST is missing;
- no fake missed-test source is created.

### W4 - G3 conflict fail-closed

Select the G3 source case.

Expected:

- after clicking Generate, the result is INFORMATION_REQUIRED;
- Resolution source shows CONFLICT;
- no Candidate is created.

### W5 - G4 idempotency

Generate G4 once, then generate the same frozen source again.

Expected:

- the second flow reports EXISTING_CANDIDATE;
- scenario identity is unchanged;
- no duplicate Candidate is created.

### W6 - G5 source revision

First generate and publish G5.

Then double-click:

`advance_quality_scenario_windows_acceptance_g5.bat`

Refresh Software Assessment and click Generate for G5 again.

Expected:

- changed source revision is detected automatically;
- generation creates a new lineage item;
- previous published scenario/history remains available.

## Evidence to capture

For each Windows acceptance item, capture:

- browser screenshot;
- visible route / page;
- scenario id when applicable;
- source completeness state;
- final status;
- any Windows-only error text.

Do not capture or submit credentials. This package does not need a real API key.

## Pass boundary

Windows compatibility is PASS when W1-W6 complete without a Windows-only product defect.

Do not judge real scenario semantic quality from this package. Real-provider semantic acceptance remains the separate macOS gate.
