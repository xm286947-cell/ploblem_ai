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

## Using the original DB

Yes. Windows acceptance supports the existing mature DB.

Preferred entry:

`start_quality_scenario_windows_original_db.bat`

You can either:

- double-click it and enter the full path of the original DB; or
- drag the original DB file onto this BAT file.

The runner first copies the original DB to:

`validation/windows_acceptance/original_quality_db_copy.db`

The application runs against the COPY. The original DB file is not modified.

In original-DB mode, the copied DB is used for both mature source data and existing QSV1 lifecycle/history so that existing scenarios remain visible. Any Review/Confirm/Publish or other test writes are written only to the copy.

The controlled-fixture mode is still available by double-clicking:

`start_quality_scenario_windows_acceptance.bat`

without a DB argument.

## Acceptance checklist

### W1 - Startup and mature navigation

Expected:

- `/issues` opens.
- `/software-assessment` opens.
- `/quality-scenarios/workbench` opens.
- `/quality-scenarios/library` opens.
- There is no requirement to enter through a P0 root.

### W2 - G1 complete source flow

Select the G1 source case and generate.

Expected:

- source preview is READY;
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

- preview state is INFORMATION_REQUIRED;
- Resolution source shows CONFLICT;
- Generate is disabled;
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

Refresh Software Assessment and preview G5 again.

Expected:

- state becomes SOURCE_CHANGED_REANALYSIS_AVAILABLE;
- generate creates a new lineage item;
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
