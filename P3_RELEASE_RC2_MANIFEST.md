# P3 Release RC2 Full Manifest

This is a fresh full source package built from the verified P3 workspace. It does not require any previous patch.

Included P3 capabilities and confirmed hotfixes:

- Historical database Dry Run → Migration Report → Apply and audit.
- HMI / PLC / IFA Preview-before-Commit E2E acceptance.
- Data Intake and Mapping UED integration.
- Mapping YAML duplicate-alias de-duplication.
- New, duplicate and single-column header recognition.
- Header rows below title/description rows.
- Mapping table sticky-header and field-label fix.
- Excel-to-ACTIVE-Mapping difference workbench.
- Alias / Product Extension / Raw Only / Ignore actions.
- Optional old Mapping disable with required-field protection.
- Direct “Open Excel and compare headers” entry in Mapping Settings.
- Multi-sheet candidate diagnostics and selection.
- Original-cell structure reading with cached-value import fallback.
- Structured Data Intake diagnostic log and diagnostic ID.
- Broken XLSX dimension metadata recalculation (`A1:A1` → actual used range).
- Standalone `tools/read_excel_headers.py` diagnostic utility.

Verification: `254 passed, 0 failed`.
