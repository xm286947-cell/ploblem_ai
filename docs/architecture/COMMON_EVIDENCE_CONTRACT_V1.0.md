# COMMON_EVIDENCE_CONTRACT_V1.0

Status: **FROZEN** (RCM-R4)

The repository has one cross-domain evidence contract: `common-evidence/v1.0`,
implemented by `contracts.common_evidence.CommonEvidence`.  Producers retain
their internal evidence objects.  A producer adapter maps an object to this
contract before it crosses a domain boundary.

The contract carries:

- `evidence_id` and `evidence_type`
- `source.source_type`, `source.source_id`, `source.source_version`
- `locator.page`, `locator.section`, and `locator.anchor`
- `excerpt` / `source_text`
- `content_hash` when applicable
- `source_ref` / `source_reference`
- `producer_domain`, `producer_object_id`, `producer_object_version`
- `verification_status`, `evidence_status`, `created_at`, and `contract_version`

The value is immutable once constructed.  A release snapshot stores the
serialized value and its release manifest hash, so consumers can perform
Evidence Trace, Source Trace, Version Trace, and immutable snapshot checks.

`CommonEvidence` has no repository or database dependency.  Consumers import
only this contract and never import a producer's internal model or table.
