# COMMON_EVIDENCE_COMPATIBILITY_POLICY_V1.0

1. `common-evidence/v1.0` is the single public Evidence contract. Existing `knowledge-evidence/v1`, Hardware Case, Historical Case, Major Issue, and Quality Scenario shapes remain producer-internal or legacy transport shapes.
2. Existing published interfaces are preserved. Migration uses a domain adapter/compatibility layer; direct replacement of a legacy payload is prohibited.
3. Additive producer fields are ignored by consumers. Required Common keys are always emitted, with `null` for unavailable facts.
4. A missing source identity, evidence identity, or producer domain is a contract error. A missing page, section, anchor, URL, source text, or hash is not an error and remains `null`.
5. A Common Evidence object is valid only when its source trace can be followed to the producer-owned source or an immutable release snapshot.
6. Common Evidence does not grant ownership or write access. Consumers are read-only and release-scoped.
7. A future incompatible shape requires `common-evidence/v2.0`, an explicit adapter, and a compatibility gate. It must not silently change v1.0 semantics.
