# STEP1 Legacy Platform Startup Verification

This document covers the two platform launchers for PR #301. It does not add a
new product entry point, runtime, Web application, or database.

## Entrypoints

| Platform | Entry | Python selection | Default database |
| --- | --- | --- | --- |
| Windows | `start_quality_capability_p1.bat` | `.venv\\Scripts\\python.exe`, then system Python 3.11 (`py -3.11` or `python3.11`) | `knowledge\\quality_issue_v1.db` |
| macOS | `start_quality_capability_p1.command` | `.venv/bin/python`, then system `python3.11` | `knowledge/quality_issue_v1.db` |

Both launchers execute the same command:

```text
main.py knowledge-web --db <selected legacy database>
```

To use an existing mature database, set `LEGACY_QUALITY_ISSUE_DB_PATH` before
launching. The path is passed through unchanged to the same legacy Web process.

## Verification matrix

Run the platform-appropriate launcher and open `http://127.0.0.1:8080`.

| Area | Route | Expected |
| --- | --- | --- |
| Existing issue workbench | `/issues` | loads in the legacy Web |
| Analysis | `/analysis` | loads in the legacy Web |
| Import | `/import` | loads in the legacy Web |
| Legacy quality scenarios | `/quality-scenarios` | list and filtering load |
| Legacy scenario assets | `/quality-scenario-assets` | asset list loads |
| Legacy portrait | `/quality-scenario-assets/portrait` | customer/industry/product portrait loads |

The launcher check is complete only when all six route checks pass against the
same selected database. Keep the server running for browser checks, then stop it
with the normal process close action. Do not use an Overall/STEP2 launcher for
this gate.

## Local preflight

The repository-side checks are intentionally platform-neutral:

```bash
zsh -n start_quality_capability_p1.command
pytest -q tests/test_step1_platform_launchers.py
```

The Windows batch file must be field-verified on Windows. The macOS `.command`
file must be double-click verified on macOS in addition to the shell syntax check.
