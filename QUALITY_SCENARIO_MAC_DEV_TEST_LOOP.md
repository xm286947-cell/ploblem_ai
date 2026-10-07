# Quality Scenario macOS Development-in-Test Loop

This is the primary development/test mode for Quality Scenario closure.

## Principle

The product package stays free of synthetic business data. This developer environment is explicitly TEST-ONLY and builds controlled source-side facts so development can enter the test environment, reproduce defects, fix them, and rerun the same G1-G5 gate.

The fixture may create only source facts. It must never pre-seed QSV1 Candidate, Review, Confirm, Publish, portrait, or history results.

## Start

```bash
./start_quality_scenario_mac_dev_loop.command
```

The launcher:
- creates/resets a dedicated controlled source DB under `validation/mac_dev_loop`;
- keeps QSV1 lifecycle output in a separate validation DB;
- starts the repository's OpenAI-compatible controlled provider mock;
- starts the mature host through `main.py knowledge-web -> quality_knowledge.web.app.create_app`;
- opens the mature Software Assessment production entry on macOS.

## Development loop

Run G1-G5 from `/software-assessment#quality-scenario-production`. When a defect is found, fix product code on the same branch, restart this environment, and rerun the failing case plus regression.

G1 complete: produce Candidate -> Review -> Confirm -> Publish -> Library/Detail -> Portrait.

G2 no missed-test: `MISSED_TEST=MISSING`; do not fabricate evidence.

G3 conflict: `INFORMATION_REQUIRED` / fail closed; do not create a bad Candidate.

G4 duplicate: same frozen source must return `EXISTING_CANDIDATE`.

G5 source revision: publish the initial scenario, then run:

```bash
./advance_quality_scenario_mac_dev_g5.command
```

Refresh Software Assessment and regenerate. A new lineage must be created while the prior published scenario/history remains.

## Platform policy

```text
PRIMARY_DEV_TEST_PLATFORM=macOS
WINDOWS=HUMAN_COMPATIBILITY_ACCEPTANCE_AFTER_MAC_BASELINE_FREEZE
REAL_DATA_GOLDEN=FOLLOW_UP_COMPATIBILITY_GATE
SYNTHETIC_DATA_IN_PRODUCT_PACKAGE=FORBIDDEN
```
