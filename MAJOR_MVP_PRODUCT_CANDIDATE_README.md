# Major MVP Product Candidate

This candidate combines the existing Major Production, Historical Case, and Repeat Risk capabilities on the existing `create_p0_app` Web host. It is a development candidate, not a release or acceptance package.

## Start

- Windows: double-click `START_MAJOR_MVP.bat`.
- macOS: double-click `START_MAJOR_MVP.command` (or run `./START_MAJOR_MVP.sh`).
- Other POSIX systems: run `./START_MAJOR_MVP.sh`.

The first start creates a package-local `.venv`, installs `requirements-major-mvp-product.txt`, and initializes an empty P0 database. The app listens on `http://127.0.0.1:8080` by default and prints all three page links in the terminal.

To change the listen address, set `MAJOR_MVP_HOST` and `MAJOR_MVP_PORT` before starting. To store writable data elsewhere, set `MAJOR_MVP_DATA_ROOT` or pass `--data-root` to `scripts/major_mvp_product_start.py`. By default all writable files live under `data/`:

```text
data/quality/db/
data/major/db/
data/major/attachments/
data/historical_case/
data/logs/
```

## AI provider configuration

The bundled model profiles contain no credential values and are intentionally unconfigured. Configure `MAJOR_MVP_AI_BASE_URL` and `MAJOR_MVP_AI_API_KEY` in the process environment and set the model name in `config/runtime/model.yaml` before using provider-backed analysis. Do not place credentials in the candidate ZIP or commit local configuration.

## Product pages

- Major Production: `/p0/major-production`
- Historical Cases: `/p0/cases`
- Issues / Repeat Risk: `/p0/issues`

This candidate does not contain real business databases, imported source documents, test fixtures/results, or local provider secrets.

## Linux E2E Gate #614

This is a complete source-backed standalone distribution; it is **not** the Windows Delta F overlay. From a fresh Linux directory execute `sh START_MAJOR_MVP.sh --data-root /tmp/isolated-major-614`. Use Python 3.11 or 3.12 and provision network/package dependencies. The built-in AI model profile is unconfigured, and no credentials are bundled. Use only an explicitly authorized provider through environment variables. Historical Repeat Agents M8.2/M8.3 and optional M8.4 are restored; their regressions must PASS on **this exact SHA**. Real Provider E2E remains BLOCKED until proven against actual model and substantive Excel+PDF source material; Linux pass does not imply Windows field pass.
