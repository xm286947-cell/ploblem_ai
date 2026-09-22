# Storage Real Provider E2E — external Runtime model config

Storage keeps the business Agent definition in Git and lets each user keep
provider endpoint/credential configuration outside the repository.

## 1. Create a private local model config

Copy `config/runtime/model.local.example.yaml` to a location outside the repo,
for example:

```bash
mkdir -p ~/runtime-config
cp config/runtime/model.local.example.yaml ~/runtime-config/model.local.yaml
chmod 600 ~/runtime-config/model.local.yaml
```

Edit only the local copy. For DashScope workspace OpenAI-compatible access,
`base_url` must be the complete HTTPS base URL ending in
`/compatible-mode/v1`.

Do not commit the local file.

## 2. Run the existing Storage Real Provider E2E

```bash
STORAGE_REAL_E2E=1 \
STORAGE_MODEL_CONFIG=~/runtime-config/model.local.yaml \
RUNTIME_PROVIDER_TRACE=1 \
pytest tests/test_agent_runtime_p0_storage_real_provider_e2e.py -q -s
```

The Storage Agent remains:

```yaml
agent_id: storage.emmc.parameter_extract
model_ref: qwen_prod
```

No business code reads URL/key directly. `AgentConfigLoader` resolves
`qwen_prod`, `ConfiguredAgentRuntime` owns execution, and the Runtime
Provider Adapter owns the actual HTTP request.

## 3. Backward-compatible CI mode

If `STORAGE_MODEL_CONFIG` is not set, the test continues to use
`config/runtime/model.yaml` and the existing
`DASHSCOPE_BASE_URL` / `DASHSCOPE_API_KEY` environment references.

## Acceptance

The test verifies:

- `model_ref=qwen_prod` resolves correctly;
- provider execution goes through Runtime;
- Storage Golden comparison passes;
- raw secret is absent from resolved config, execution snapshot, result and
  Runtime SQLite bytes;
- Runtime provider-call accounting remains active.

The test no longer requires URL/key to originate from any fixed environment
variable names.
