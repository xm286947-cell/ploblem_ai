# COMMON_EVIDENCE_COMPATIBILITY_POLICY_V1.0

Status: **FROZEN** (RCM-R4)

`common-evidence/v1.0` is the only public cross-domain Evidence Contract.
Existing `knowledge-evidence/v1`, Runtime `EvidenceReference`, Storage
Evidence, Hardware Case evidence, Historical Case evidence, and Quality
Scenario evidence remain valid producer-owned contracts.  They are adapted at
the boundary; none is deleted or silently reinterpreted.

Compatibility rules:

1. Consumers validate `contract_version` and fail closed on an unsupported
   version.
2. Producers may add internal fields without changing the common contract.
3. A breaking public change requires `common-evidence/v2.0`, a new adapter, and
   an explicit consumer binding; it cannot be introduced by changing v1.0 in
   place.
4. `source_id`, `source_version`, `source_ref`, producer identity, and content
   hash must remain traceable through every mapping.
5. Consumers must not access producer databases, repositories, internal models,
   or tables.
