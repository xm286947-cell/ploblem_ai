# KNOWLEDGE_QUALITY_ISSUE_ANALYSIS_ENGINE V1.0 M5 RC2

## Fixed

### AI-CONFIG-001
Quality Issue Analysis now loads the same root `config/model.yaml` used by the Engine. `quality_issue_ai` may override shared `ai` fields while inheriting missing fields.

### AI-DIAG-001
Added:

```bash
python main.py knowledge-ai-check
python main.py knowledge-ai-check --live
```

The command reports the exact config path, enabled/provider/base_url/model/api_key_env and whether the environment variable exists. Secrets are never printed.

### FIELD-CONFIG-002
HMI / PLC / IFA adapters are now YAML-driven instead of maintaining a second hard-coded field mapping.

Configured fields are persisted as follows:

- typed fields -> IssueFact / ProductContext / Occurrence / Escape / Solution / Verification;
- configured fields not yet represented by a typed DTO -> `product_extension.typed_model_pending`;
- all configured & matched fields -> `product_extension.configured_fields`;
- all original fields remain in Raw JSON.

### AI-PERSIST-001
Verified Analysis Run and AI result persistence across repository/process recreation. Latest valid result remains queryable after restart.

## Validation

- RC2 dedicated tests: 3 passed
- Full project regression: 155 passed, 0 failed
- Real PLC header smoke: header row 1, PLC score 69, total 1, new 1, failed 0
- AI config diagnostic smoke: config/model.yaml resolved correctly
