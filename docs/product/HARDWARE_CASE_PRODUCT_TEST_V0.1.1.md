# Hardware Case Product Test V0.1.1

Status: **READY_FOR_INTERNAL_TEST after Gate**  
Release claim: **NO**  
Package: `HARDWARE_CASE_PRODUCT_TEST_V0.1.1_<commit>.zip`

V0.1.1 is the package-fix revision that closes the two usability gaps found in
V0.1:

1. no obvious product start entry;
2. no packaged Hardware Case Agent / Unified Runtime model configuration path.

## What changed from V0.1

The package now contains these root-level entries:

```text
INIT_LOCAL_CONFIG.bat
CHECK_ENV.bat
START_HARDWARE_CASE.bat
RUN_REAL_AI_VALIDATION.bat

INIT_LOCAL_CONFIG.sh
START_HARDWARE_CASE.sh
RUN_REAL_AI_VALIDATION.sh
```

And the Runtime/Agent assets:

```text
config/runtime/
├─ model.local.hardware_case.example.yaml
└─ agents/
   └─ hardware_case.structure.yaml

prompts/runtime/hardware_case/
└─ structure_v1.md

services/
└─ hardware_case_runtime_adapter.py
```

The product still uses the existing Unified Runtime. No second Provider, Retry,
Secret, endpoint resolver, or Runtime implementation is introduced.

## First-time setup on Windows

1. Extract the ZIP into a clean local directory.
2. Run:

```bat
INIT_LOCAL_CONFIG.bat
```

This creates, without overwriting existing local files:

```text
config\runtime\model.local.yaml
config\hardware_case_real_validation.local.json
data\input\word\
data\tree\
data\output\
data\runtime\
```

3. Edit `config\runtime\model.local.yaml`.

Example:

```yaml
active_model: hardware_case_real

models:
  hardware_case_real:
    provider: openai_compatible
    base_url: https://COMPANY_APPROVED_ENDPOINT/v1
    api_key_env: HARDWARE_CASE_API_KEY
    model: COMPANY_APPROVED_MODEL
    temperature: 0
    max_tokens: 8192
```

4. Set the API key only in the local environment, for example:

```bat
set HARDWARE_CASE_API_KEY=YOUR_LOCAL_KEY
```

Do not put a real API key back into GitHub or Drive package templates.

## Environment check

Run:

```bat
CHECK_ENV.bat
```

It checks:

- Python 3.11+
- required Python modules
- P07 frontend package files
- writable local data/runtime directories
- `model.local.yaml`
- active model / endpoint / model name
- required API key environment variable
- `hardware_case.structure` Agent config
- Unified Runtime config resolution

Result must be:

```text
RESULT=PASS
MODE=all
```

You can check only Web startup:

```bat
CHECK_ENV.bat web
```

or only Real AI:

```bat
CHECK_ENV.bat real-ai
```

## Start the product

Windows:

```bat
START_HARDWARE_CASE.bat
```

The script:

1. runs the Web precheck;
2. starts the existing unified Quality Capability Web;
3. opens the browser;
4. uses the existing P0/P1 app chain and does not start a second Web server.

P07:

`http://127.0.0.1:8080/p0/hardware-cases/base-data`

Linux/macOS:

```bash
sh START_HARDWARE_CASE.sh
```

## Real AI validation

Put company-local test inputs under or point the local config to:

```text
data/input/word/
data/tree/circuit_feature.xlsx
data/tree/material_device.xlsx
```

Edit:

`config/hardware_case_real_validation.local.json`

Then run:

```bat
RUN_REAL_AI_VALIDATION.bat
```

Execution path:

```text
Real Word
  -> DOCX Parser
  -> services.hardware_case_runtime_adapter
  -> Unified Runtime
  -> hardware_case.structure Agent
  -> model.local.yaml
  -> OpenAI-compatible Provider
  -> strict JSON Candidate
  -> Evidence grounding
  -> Hardware Case validation report
```

The Runtime owns endpoint resolution, secret loading, retry budget, provider-call
limits, and strict JSON/schema validation.

The Hardware Case side owns only the business Prompt, output schema and
Candidate/Evidence semantics.

## Agent behavior

Agent ID:

`hardware_case.structure`

The Agent may extract Candidate fields such as:

- symptom
- impact
- occurrence condition
- analysis process
- failure mode
- root cause
- failure mechanism
- actions
- verification result
- conclusion

Core facts `symptom / root_cause / actions` must have source Evidence block IDs
when populated.

The Agent may not:

- create confirmed values;
- publish a case;
- invent Evidence block IDs;
- invent formal tree node IDs;
- auto-confirm mappings.

## P07 / Tree test scope

V0.1.1 preserves the frozen P07 product rules:

- ADD / UPDATE / RENAME / MOVE / DEPRECATE / NO_CHANGE / CONFLICT
- DELETE is not a Tree Change Type
- EXCLUDE is only a ChangeSet decision
- APPLIED_WITH_EXCLUSIONS is not a partial Apply failure
- APPLY_FAILED creates no new ACTIVE Version
- Excel omission does not mean delete
- historical Case mapping stays on stable node_id

## Offline package smoke

```bat
python scripts\hardware_case_product_test_smoke.py
```

Expected:

```text
RESULT=PASS
PRODUCT_TEST_PACKAGE=PASS
P07_FRONTEND=PASS
PACKAGE_STATUS=READY_FOR_INTERNAL_TEST
RELEASE_CLAIM=NO
```

## Security

Real company Word/Excel/images and real provider credentials are not bundled.

The package excludes:

- SQLite runtime/business databases
- logs
- .env
- real `model.local.yaml`
- local secret Agent files
- real API keys / Authorization
- real provider raw content / prompts from previous runs

Only safe example configuration is packaged.

## Still not claimed

This is still an internal test package, not a Release package.

Open acceptance items remain:

- REAL_TREE_IMPORT_VALIDATION
- M4_REAL_DATA_VALIDATED
- AI_INTEGRATION_GATE_PASS
- 20_TO_30_REAL_CASE_MVP_INTEGRATION_GATE_PASS
- HC_TREE_IMPORT_PRODUCT_GATE
- MVP_INTEGRATION_GATE
- MVP_DEMO_GATE
- RELEASE_GATE

P01-P06 Hardware Case dedicated formal frontend is still outside this package.

## Test callback

```text
RESULT = PASS / PARTIAL / BLOCKED
PACKAGE =
SOURCE_COMMIT =
ENVIRONMENT =
WEB_START =
AGENT_CONFIG =
RUNTIME_CONFIG =
REAL_PROVIDER =
P07_TREE_IMPORT =
REAL_TREE_IMPORT =
REAL_AI =
REAL_CASE_E2E =
SECURITY_BLOCKERS =
DEFECTS =
KNOWN_ISSUES =
NEXT_GATE =
```
