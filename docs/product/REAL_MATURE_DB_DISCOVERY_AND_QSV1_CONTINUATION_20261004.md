# Real Mature DB Discovery and QSV1 Continuation — 2026-10-04

## Running process inspection

```text
PORT=8082
PID=49942
ACTUAL_PROCESS_CWD=/private/var/folders/tz/ms6n8bjx3jn14msjq65pz62h0000gn/T/step1b-native-preview.XXXXXX.nkMHuVPG2W/candidate
ACTUAL_SOURCE_SHA=9153751d2d68a6bd877b7f7785dea6aeb47430ad
ACTUAL_DB_ABSOLUTE_PATH=/private/var/folders/tz/ms6n8bjx3jn14msjq65pz62h0000gn/T/step1b-native-preview.XXXXXX.nkMHuVPG2W/candidate/knowledge/quality_issue_v1.db
```

The process command is `main.py knowledge-web --db <package-local database> --host 127.0.0.1 --port 8082`. Its working directory is an extracted Preview Candidate, not the PR #301 worktree. `STEP1B_SOURCE_COMMIT` in that package confirms SHA `9153751...`; it is not the reported `35baf462...` source.

Read-only SQLite inspection found the candidate database has zero `quality_issue`, `quality_issue_version`, `source_material`, and `product_quality_report` rows, and has no legacy `quality_scenario` table. It is not a mature business data source.

## Discovery scope and candidates

The search covered matching `quality_issue_v1.db` files under `/Users/xiamin`, project/worktree files in `~/.codex`, `Documents/Codex` historical test locations, and the active `/private/var/folders/.../T` Preview package. It also inspected the historical legacy-runtime acceptance database documented in its saved evidence.

| Candidate | Provenance / classification | Issue | Issue versions | Source materials | Legacy scenarios | Result |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| `/Users/xiamin/.codex/.chatgpt-projects/g-p-6aaeb68a97148191b922a97427382781/ploblem_ai/knowledge/quality_issue_v1.db` | PR #301 worktree at `35baf462...` | 0 | 0 | 0 | 0 | Empty |
| `/Users/xiamin/Documents/Codex/2026-09-29/mac-step1-startup-check/ditto-r1/knowledge/quality_issue_v1.db` | Historical macOS startup-check copy | 0 | 0 | 0 | 0 | Empty |
| `/private/var/folders/tz/ms6n8bjx3jn14msjq65pz62h0000gn/T/step1b-native-preview.XXXXXX.nkMHuVPG2W/candidate/knowledge/quality_issue_v1.db` | Active 9153751 Preview Candidate | 0 | 0 | 0 | table absent | Empty candidate DB |
| `/Users/xiamin/.codex/.chatgpt-projects/g-p-6ab5008ec7a08191bc52cc88736f4608/work/quality-scenario-v11-r1-macos-formal-retest-001/fresh-extract-20260925-221833/browser-formal-e2e/db/legacy.sqlite` | Historical acceptance evidence explicitly says `quality_issue=0` | 0 | 0 | 1 | 0 | Not mature business data; no scenarios/reports |

The old `legacy.sqlite` also has zero reports and zero scenario-generation/asset-context records. Its one material and one taxonomy-version row do not establish actual mature scenario content.

Historical GitHub Actions run `36601031937` was inspected because prior notes described its macOS gate as showing mature data. Its job explicitly deletes and seeds a `STEP1-MAC-*` scenario, evidence and generation-source row; that is synthetic CI data and is excluded from product evidence. It is not a local mature database.

```text
REAL_MATURE_DB_SOURCE=NOT_FOUND_AFTER_SEARCH
REAL_MATURE_DB_PATH=NONE
VALIDATION_COPY=NOT_CREATED
```

No candidate was modified. No empty/test database was promoted to the real-data gate.

## QSV1 additive continuation

Per task clarification, QSV1 integration proceeded independently of the unavailable mature dataset. The mature host now marks its QSV1 shell context, and `p0_base.html` routes back to the mature `/issues`, `/analysis`, ITR, software-assessment, missed-test, report, import, and restored legacy scenario/configuration routes. Its QSV1 menu does not direct users to the condensed `/p0/itr-*`, `/p0/software-assessment`, or `/p0/missed-test-analysis` duplicates. The existing QSV1 mount, V1 database binding, workflow, library/detail/source/evidence/history and published-only P04 provider were retained.

Focused regression command covered:

- `test_step1b_qsv1_mature_preview.py`
- `test_r2_w2_qsv1_production_chain.py`
- `test_quality_scenario_workspace_binding.py`
- `test_p04_portrait_archive.py`
- `test_p04_quality_scenario_insight.py`
- `test_p04_real_integration.py`
- `test_qs_p04_p03_published_binding_fix.py`
- `test_r2_w2_legacy_scenario_compat.py`
- `test_r2_w2_projection_parity.py`

Result: `41 passed`. These are focused structural/workflow contract tests with isolated test databases; they do not count as real mature data validation.

```text
MATURE_PLATFORM_RESTORE=BLOCKED_PENDING_REAL_MATURE_DATABASE
EXISTING_CAPABILITY_REGRESSION=BLOCKED_PENDING_REAL_MATURE_DATABASE
QSV1_ADDITIVE_INTEGRATION=PASS (focused regression only)
WINDOWS_NATIVE_GATE=NOT_STARTED
MACOS_NATIVE_GATE=NOT_STARTED
PREVIEW_CANDIDATE=NOT_BUILT
8082_PROCESS=LEFT_RUNNING_UNCHANGED
```
