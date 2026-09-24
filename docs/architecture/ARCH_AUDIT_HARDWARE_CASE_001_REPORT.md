# ARCH_AUDIT_HARDWARE_CASE_001_REPORT

Baseline: `main@654b7102ecfbb9e0a89cff354574c2a77551c713`

Status: DOMAIN_BOUNDARY_AUDIT_PASS / POST_MERGE_GATE_PASS / CLOSED

## Audit rule

Every dependency reachable from the Hardware Case product entry is classified as:

- `HARDWARE_OWNED`
- `PLATFORM_SHARED`
- `ILLEGAL_CROSS_DOMAIN_COUPLING`

P01-P07, `hardware-case/v1`, `hardware-tree-import/v1`, Publish Gate, dual-tree semantics and the Word -> Candidate -> Evidence -> Review -> Publish Golden Path are protected baselines and are not redesigned by this audit.

## Findings

| Source | Target | Classification | Evidence | Remediation |
| --- | --- | --- | --- | --- |
| `services/hardware_case_*.py` | Hardware Case repository/service/contracts | HARDWARE_OWNED | direct product-domain implementation | none |
| `repositories/hardware_case_repository.py` | SQLite | HARDWARE_OWNED | product-owned persistence, no AI/Web/domain imports | none |
| `repositories/hardware_tree_import_repository.py` | Hardware tree version/change persistence | HARDWARE_OWNED | independent tree import persistence | none |
| `quality_knowledge/web/hardware_case_api.py` | Hardware Case service | HARDWARE_OWNED | thin API boundary under existing `/api/v2` | none |
| `quality_knowledge/web/hardware_tree_import_api.py` | Hardware Tree import service/repository | HARDWARE_OWNED | P07 backend entry | none |
| `services/hardware_case_runtime_adapter.py` | `runtime/*` | PLATFORM_SHARED | Provider/config/retry/secret remain owned by Unified Runtime | none |
| `scripts/hardware_case_web_start.py` | full `create_p0_app` initialization | ILLEGAL_CROSS_DOMAIN_COUPLING | Hardware Case startup forced P0 Quality Issue DB + Repeat Risk initialization | add explicit `enabled_domains={"HARDWARE_CASE"}` composition |
| `quality_knowledge/web/p0_app.py` module imports | Quality Issue / Repeat Risk modules | ILLEGAL_CROSS_DOMAIN_COUPLING | unrelated business modules loaded before product composition decision | move unrelated imports behind domain gate |
| `services/__init__.py` | Knowledge Service + Historical Case | ILLEGAL_CROSS_DOMAIN_COUPLING | importing any Hardware Case service executed unrelated domain imports | lazy public exports |
| `quality_knowledge/web/__init__.py` | legacy full Web app | ILLEGAL_CROSS_DOMAIN_COUPLING | importing Web package eagerly loaded full business Web implementation | lazy app-factory exports |
| `repositories/__init__.py` | JsonArtifactRepository | ILLEGAL_CROSS_DOMAIN_COUPLING for product package | Hardware repository import executed unrelated repository export | lazy public exports |
| Hardware Case product package | whole `quality_knowledge/repositories/services/schema` trees | ILLEGAL_CROSS_DOMAIN_COUPLING | product artifact bundled unrelated domain source | explicit Hardware Case allowlist + Unified Runtime subtree only |

## Remediation constraints applied

1. Default `create_p0_app(...)` remains FULL composition.
2. Hardware Case product startup uses the same FastAPI composition root, same `/api/v2` convention and same port.
3. No second Provider, Runtime, Web server or business database is introduced.
4. P01-P07 page functions are reused; Hardware Case-only composition filters the existing page router rather than redefining product behavior.
5. Hardware Case package no longer relies on whole-business-directory copying.
6. Boundary tests assert that Quality Issue, Repeat Risk and Historical Case modules are not imported by Hardware Case-only startup.

## Gate evidence

Validated on PR #107 head `cf3b72d84a37a3f827c9ad18319785d622d96610`:

- Hardware Case Product Test Package: PASS; regression suite `82 passed, 1 warning`.
- Explicit package allowlist build: PASS.
- Package manifest boundary assertions: PASS.
- Packaged Hardware Case-only startup: PASS.
- Hardware Case Web API Integration: PASS.
- Hardware Case Product API E2E: PASS.
- Hardware Case M3 P01-P07 Frontend: PASS.
- Evidence Source: PASS.
- Hardware Tree Import M3A + M3B: PASS.
- Hardware Case Release Prep: PASS.
- Major Issue Runtime Config + Major Case Publish regression: PASS.
- Repeat Risk functional H01-H10/E2E, ITR, domain, Historical Case and Case Publish regression steps: PASS.
- Product test artifact: `HARDWARE_CASE_PRODUCT_TEST_FULL_V0.1_abe7b64c4966.zip`.
- Product ZIP SHA256: `626d7422982f4dbe5939ca17e96cd53b98ca33f4f567b55d14e58a8ffa404371`.

Closure gates:

- DOMAIN_BOUNDARY_STATIC_GATE
- HARDWARE_CASE_ONLY_STARTUP_GATE
- PACKAGE_BOUNDARY_GATE
- P01_P07_REGRESSION_PASS
- GOLDEN_PATH_REGRESSION_PASS
- DEFAULT_FULL_COMPOSITION_REGRESSION_PASS

Final result: `DOMAIN_BOUNDARY_AUDIT_PASS`.

PR #107 is ready for review/merge; no further architecture redesign is required by this audit.


## Post-merge closure

- PR #107 merged to `main@1c51bf3e030090804e743df6c205b572e3b61f05`.
- Main push Gate: Hardware Case Product Test Package PASS.
- Main push Gate: Hardware Case M3 Frontend PASS.
- Main push Gate: Hardware Case Evidence Source PASS.
- Main package: `HARDWARE_CASE_PRODUCT_TEST_FULL_V0.1_1c51bf3e0300.zip`.
- Main package SHA256: `328984b6ce84755563a2c6fb681915c5eb23c37ef61fda3ce8d3ec286789a66b`.
- Architecture audit task is CLOSED. Any future coupling regression must be caught by the package/domain-boundary CI gate rather than reopened as redesign work.
