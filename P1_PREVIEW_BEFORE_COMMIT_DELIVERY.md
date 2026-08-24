# P1 PREVIEW-BEFORE-COMMIT DELIVERY

## IMPLEMENTATION_STATUS
COMPLETED

## Scope
P1 only: close DEP-DI-01. Existing A1-A5 Mapping capability is reused.

## Implemented
- Web `/import` no longer commits immediately.
- Added `/import/preview` -> temporary intake session -> Mapping/Coverage preview.
- Added `/import/confirm` -> explicit commit.
- Added API `/api/import/preview` and `/api/import/confirm`.
- Auto-detects HMI/PLC/IFA using the existing effective DB Mapping configuration.
- Preview records the exact ACTIVE mapping config id/version.
- Confirm rejects stale previews if ACTIVE Mapping changed after preview.
- Conflict or Required Missing blocks commit.
- Repeated confirm of an already committed session is idempotent and returns the prior batch.
- Uploaded preview file is kept in a temporary intake-session area; Preview itself does not write Knowledge.
- Existing `/api/issues/import` is retained for API compatibility; P1 changes the Web user flow.

## Modified / Added
- quality_knowledge/web/app.py
- quality_knowledge/services/intake_session_service.py
- quality_knowledge/web/templates/import.html
- quality_knowledge/web/templates/import_preview.html
- tests/test_p1_preview_before_commit.py

## Database / Contract
- No DB schema migration.
- No Knowledge schema change.
- No Mapping schema change.
- New temporary intake session is filesystem-backed.
- New Web/API contracts: preview + confirm.

## Test Result
Focused P1 + A3/A4/A5 regression:
9 passed, 0 failed.

## Compatibility
- Existing Mapping DB/version/preview/coverage reused.
- Existing direct API `/api/issues/import` retained.
- CLI import unchanged.
- No re-import required for existing Knowledge.

## Next
P2: Data Intake / Mapping UED integration. Do not rebuild A1-A5.
