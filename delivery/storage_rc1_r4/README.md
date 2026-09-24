# Storage RC1 R5 Delivery Quality Source

Task: `STORAGE-RC1-R5-LAUNCHER-PORT-OWNERSHIP-FIX-001`.

This directory remains the repository-backed delivery source of truth. The failed R4A ZIP is a frozen, SHA-pinned input only and is permanently `FAILED_RETIRED`; it is not treated as source code.

## Official launch contract

- Linux/macOS: `bash start_test.sh mock` / `bash start_test.sh real`
- Windows: `run_windows.bat`
- `STORAGE_WEB_PORT` remains configurable; default is 8765.
- Mock-only ports are 18000 (OpenAI Mock) and 18001 (Storage Mock Router).

## Port ownership contract

R5 must never accept a health response from a process it did not start.

Before any product/mock service is spawned:
- Storage Web target port must be bind-free.
- In mock mode, 18000 and 18001 must also be bind-free.
- Conflict is fail-fast with `PORT_CONFLICT`, `PORT=<n>`, `SERVICE=<name>`, and `PRODUCT_E2E=NOT_RUN`.

After each background service is spawned, readiness requires all three:
1. spawned PID is still alive;
2. that PID owns the expected listening TCP port;
3. expected health URL succeeds.

Health-only readiness is forbidden. A dead spawned PID cannot borrow another process's health endpoint.

Launcher lifecycle ownership also applies at shutdown: every background PID started by the launcher is terminated and waited before the launcher returns, so a successful scenario cannot leave its own listener behind for the next scenario.

## Automated port cases

- `TEST-PORT-01`: 8765 occupied -> fail-fast -> Product E2E not run.
- `TEST-PORT-02`: 18000 occupied -> fail-fast -> Product E2E not run.
- `TEST-PORT-03`: 18001 occupied -> fail-fast -> Product E2E not run.
- `TEST-PORT-04`: all ports free -> normal mock Product E2E pass.
- `TEST-PORT-05`: spawned PID dies while another health endpoint is reachable -> launcher fails.

The cases are implemented by `scripts/port_guard.py`, `scripts/port_regression.py`, and are executed by `selfcheck.sh`.

## Package contract

- every `.sh` ZIP entry carries Unix regular-file mode `0755`;
- runtime behavior does not depend on extractor permission restoration;
- shell-to-shell calls use explicit `bash`;
- no manual `chmod +x` repair is allowed;
- UTF-8 path flags, SHA manifest, provenance, package identity, builder version, and R5 port-ownership semantics are enforced by `tools/package_contract_gate.py`.

R5 build provenance is mandatory:
- `SOURCE_BRANCH`
- 40-hex `SOURCE_COMMIT`
- `BUILD_SCRIPT_VERSION=storage-rc1-r5-builder-v1.0`

Release ceiling is `READY_FOR_PLATFORM_RETEST`. Development must not claim `TEST_PASS`, `PRODUCT_GATE_PASS`, or `RC1_PASS`.
