# ADR-007 Failure, Recovery and Idempotency
STATUS=ACCEPTED

Decision:
- Normalize failures into VALIDATION, DATA_INTEGRITY, DEPENDENCY_UNAVAILABLE, AUTHORIZATION, CONFLICT, TRANSIENT and INTERNAL.
- Retry only transient idempotent operations.
- Preserve atomic Tree Apply.
- Preserve stable Knowledge publish idempotency key and public-ref reconciliation.
- Never blindly repeat a publish after an uncertain external result.
- Migration uncertainty blocks startup.
- Failed Runtime/AI attempts cannot replace the last valid confirmed/published state.
