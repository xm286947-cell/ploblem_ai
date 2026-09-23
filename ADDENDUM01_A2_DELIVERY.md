# ADDENDUM01 A2 DELIVERY

Version: KNOWLEDGE_QUALITY_ISSUE_ANALYSIS_ENGINE_V1.0_ADDENDUM01_A2_RC1
Scope: GAP-A2 YAML Migration Tool

## Implemented
- `knowledge-mapping-migrate --all --dry-run`
- `knowledge-mapping-migrate --business PLC --file ... --dry-run`
- `knowledge-mapping-migrate --all --apply`
- Legacy YAML Loader -> Mapping DTO -> Validation -> Preview -> Apply -> Versioned Mapping DB
- business_type + source_hash idempotency
- First migration auto-activates only when no ACTIVE config exists
- Changed YAML creates a new DRAFT when an ACTIVE version already exists
- Migration audit persisted in `mapping_migration_run`

## Dry-run fields
Business Type, Canonical Field Count, Source Header Count, Alias Count, Target Field Count,
Valid/Warning/Conflict/Invalid Count, Source Hash, Migration Result.

## Validation
HMI: 36 canonical fields / 47 headers / 0 invalid
PLC: 60 canonical fields / 111 headers / 0 invalid / alias ambiguity warning for `变更影响`
IFA: 49 canonical fields / 69 headers / 0 invalid

## Tests
A2专项: 3 passed
Full regression: 174 passed / 0 failed

## Boundary
A2 does not switch import runtime to DB mapping. Runtime switch remains GAP-A3.
