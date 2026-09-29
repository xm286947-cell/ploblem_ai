# Hardware W3 Development Packages V1.0

SOURCE=W2_ARCHITECTURE_BASELINE_V1
STATUS=READY_FOR_EXECUTION
PRESERVED_CAPABILITIES=14/14

## W3.1 RELEASE_AND_OPERABILITY
MUST_IMPLEMENT:
1. Bind hardware-public-consumer/v1 to /api/public/hardware/v1.
2. Add v1 compatibility/schema regression at HTTP boundary.
3. Add macOS .command wrapper delegating to shared shell launcher.
4. Normalize package manifest for Windows/macOS launcher parity.
5. Add machine-readable Hardware health/readiness.
6. Add config/secret precheck contract with safe error codes.
7. Keep same Web host/port and same Hardware-only composition.

ACCEPTANCE:
- public HTTP contract golden path PASS;
- Hardware-only boundary PASS;
- Windows launcher smoke PASS;
- macOS/POSIX launcher smoke PASS;
- health/readiness dependency-state tests PASS;
- no secret values in diagnostics/logs;
- 14/14 preserved.

## W3.2 DATA_RELIABILITY
MUST_IMPLEMENT:
1. Hardware schema version metadata.
2. Ordered transactional migrations.
3. Pre-migration verified backup.
4. Backup manifest with DB/source consistency metadata.
5. Restore command/service with integrity verification.
6. Restore-based rollback and fail-closed startup on migration uncertainty.

ACCEPTANCE:
- fresh DB / current DB / old fixture migration PASS;
- injected migration failure leaves original state recoverable;
- backup/restore roundtrip PASS;
- source hash mismatch fails closed;
- 14/14 preserved.

## W3.3 PRODUCTION_RELIABILITY
MUST_IMPLEMENT:
1. Trusted auth resolver interface and role enforcement.
2. correlation_id propagation.
3. structured event/log schema and safe diagnostic export.
4. normalized failure classes.
5. recovery/idempotency tests for Runtime/Knowledge uncertain outcomes.
6. benchmark fixtures and capacity/performance gate.

ACCEPTANCE:
- no role escalation;
- correlation chain visible end-to-end;
- secrets/source content absent from diagnostics;
- duplicate publish prevented/reconciled;
- performance/capacity budgets pass or produce explicit architecture blocker;
- 14/14 preserved.

W3_SEQUENCE=W3.1 -> W3.2 -> W3.3
OPEN_ARCH_BLOCKER=0
READY_FOR_W3_1=YES
