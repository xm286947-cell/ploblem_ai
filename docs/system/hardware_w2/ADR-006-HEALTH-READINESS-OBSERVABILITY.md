# ADR-006 Health, Readiness, Observability and Correlation
STATUS=ACCEPTED

Decision:
- Separate liveness from readiness.
- Readiness reports HARDWARE_DB, SOURCE_REGISTRY, ACTIVE_TREE_STATE, UNIFIED_RUNTIME_CONFIG, UNIFIED_KNOWLEDGE and PUBLIC_CONTRACT.
- Expose machine-readable dependency state and error codes, not secrets.
- Generate/propagate `correlation_id` through Intake -> Runtime -> Review -> Publish -> Knowledge.
- Structured logs contain metadata only; raw source/provider content is forbidden.
- Support metadata-only diagnostic export.
