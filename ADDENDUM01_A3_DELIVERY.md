# KNOWLEDGE_QUALITY_ISSUE_ANALYSIS_ENGINE_ADDENDUM01_A3_DELIVERY

Version: ADDENDUM01_A3_RC1
Status: Delivered
Baseline: ADDENDUM01_A2_RC1
Scope: GAP-A3 Runtime Switch

## Result
- ACTIVE Mapping Configuration in SQLite is now the runtime source for KnowledgeIssueService imports.
- Web and CLI imports share the same service and effective mapping.
- No runtime YAML fallback: missing ACTIVE config raises `MAPPING_NOT_INITIALIZED` with migration guidance.
- Configurable adapters consume DB MappingConfiguration, not `*_fields.yaml`.
- Header detection and mapping coverage are calculated from ACTIVE DB mappings.
- Each new `quality_issue_version` records `mapping_config_id` and `mapping_config_version`.
- Legacy IssueImportService also requires ACTIVE DB mapping to avoid a second YAML runtime path.

## Database change
`quality_issue_version` gains nullable compatibility columns:
- mapping_config_id TEXT
- mapping_config_version INTEGER
Existing rows remain unchanged; no destructive migration.

## Acceptance
- YAML -> A2 migration -> ACTIVE DB config -> CLI import: PASS
- DB mapping coverage 100% smoke case: PASS
- Missing ACTIVE config -> MAPPING_NOT_INITIALIZED: PASS
- Mapping version trace on Issue Version: PASS
- Full regression: 176 passed, 0 failed

## Next
GAP-A4 Mapping Web.
