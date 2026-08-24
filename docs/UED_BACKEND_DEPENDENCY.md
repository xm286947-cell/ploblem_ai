# UED Backend Dependency

## DEP-01 Preview-before-Commit Import
Current `/import` writes directly to Knowledge after submit. UI therefore presents Mapping Preview as a recommended pre-check and does not fake a confirm-before-commit flow.

## DEP-02 Statistics Drill-down Filter
Implemented for recurrence risk, AI status and human status in Issues presenter. Category-specific Occurrence/Escape/Gap query drill-down remains a backend query enhancement candidate.

## DEP-03 Human Auto-save
Not required in V1. Explicit Save remains authoritative.

## DEP-04 Human Field Drag Ordering
Not implemented. Existing numeric display_order remains authoritative.
