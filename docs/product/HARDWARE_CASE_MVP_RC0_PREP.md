# Hardware Case MVP RC0 PREP

Status: **PREP_ONLY_NOT_MVP_RELEASE**

This artifact prepares packaging and smoke validation for Hardware Case MVP V0.1.
It is intentionally not a product-release declaration.

## Included
- hardware-case/v1 contract and backend
- SQLite repository
- Publish Gate and PUBLISHED-only consumer rules
- DOCX/AI Adapter integration boundary
- Unified `create_p0_app` / `/api/v2/hardware-cases` API
- company-only Real Validation Harness
- synthetic package smoke

## Release blockers still open
- HIFI_PRODUCT_GATE_PASS
- FRONTEND_MVP_GATE_PASS
- M4_REAL_DATA_VALIDATED
- AI_INTEGRATION_GATE_PASS
- 20–30 real-case MVP_INTEGRATION_GATE_PASS

## Security
The build script excludes databases, logs, .env, local/secret YAML, and any real
company source materials. Real Word, Excel, images, customer/project/model data
must remain in the company-controlled environment.

## Build
`python scripts/build_hardware_case_mvp_package.py`

Expected archive:
`dist/HARDWARE_CASE_MVP_RC0_PREP_<commit>.zip`

The package manifest must say:
`release_status = PREP_ONLY_NOT_MVP_RELEASE`

## Smoke
Windows:
`run_hardware_case_mvp_smoke.bat`

Linux/macOS:
`./run_hardware_case_mvp_smoke.sh`

Smoke success proves package integrity and the synthetic backend/API Golden Path
only. It does not prove HIFI frontend completion, Real Provider accuracy, or
MVP release readiness.
