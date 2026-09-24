# ARCH_AUDIT_HARDWARE_CASE_001_REPORT

Baseline: `main@654b7102ecfbb9e0a89cff354574c2a77551c713`

Status: REMEDIATION_IMPLEMENTED / CI_PENDING

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

## Gate

Required before closure:

- DOMAIN_BOUNDARY_STATIC_GATE
- HARDWARE_CASE_ONLY_STARTUP_GATE
- PACKAGE_BOUNDARY_GATE
- P01_P07_REGRESSION_PASS
- GOLDEN_PATH_REGRESSION_PASS
- DEFAULT_FULL_COMPOSITION_REGRESSION_PASS

Final target: `DOMAIN_BOUNDARY_AUDIT_PASS`.
