# Runtime Provider Strict-JSON Contract & Recovery V1.0

Status: DEVELOPMENT / PR #41

## Boundary

Business defines Prompt, input/output schema and domain rules.
Runtime owns Provider request construction, capability handling, evidence, strict JSON parsing, deterministic recovery, schema validation and retry ownership.

Storage business code must not implement Provider calling or JSON repair.

## Provider evidence

Every Runtime Provider Attempt persists content-safe evidence in:

`AttemptRecord.execution_metrics.provider_evidence`

Recorded evidence includes:

- resolved model / model_ref
- agent/model config source and agent config hash
- resolved max_tokens
- actual request max_tokens / max_completion_tokens
- structured-output capability and actual request mode
- HTTP status / provider request id
- raw Provider usage / finish_reason
- content length/hash
- streaming/chunk diagnostic state
- recovery hashes and recovery type when applicable

The evidence intentionally excludes Prompt text, business input/PDF text, API keys, Authorization values, and full Provider content.

## Structured-output capability

Capabilities are Provider-profile metadata. Example:

```yaml
models:
  company_prod:
    provider: openai_compatible
    base_url: https://provider.example/v1
    api_key_env: COMPANY_API_KEY
    model: company-model
    max_tokens: 8192
    metadata:
      capability_source: CONFIGURED
      capabilities:
        structured_output: json_schema
        token_limit_parameter: max_tokens
```

Supported `structured_output` declarations:

- `json_schema`
- `json_object`
- `unsupported`

If capability is unknown or unsupported, Runtime records the state and uses `PROMPT_ONLY` fallback. Runtime never reports JSON mode as enabled unless the actual request contains the corresponding `response_format`.

For array-shaped output, `json_object` alone is not requested because it conflicts with the required array shape.

## Recovery policy

### TRANSPORT_RECONSTRUCTION

Only complete sequenced chunks may be reordered deterministically. Duplicate or missing sequence values fail closed.

After reconstruction Runtime must run:

`strict json.loads -> output shape -> schema validation`

### DETERMINISTIC_WRAPPER_RECOVERY

Runtime may extract one unique complete object/array when the JSON itself is unchanged and only wrapper text exists, such as Markdown fences or explanatory text.

It never repairs brackets, strings, values, fields or business semantics.

After extraction Runtime reruns strict JSON parsing and schema validation.

### SEMANTIC_REPAIR_REQUIRED

Truncated containers, broken strings, missing data, ambiguous multiple JSON values, or any case requiring guessing fails with a validation error. No repaired result is returned to business code.

## Streaming/chunks

The current OpenAI-compatible Storage Provider Adapter is non-streaming and records:

- `streaming=false`
- `chunk_diagnostics=NOT_APPLICABLE`

The Runtime Provider layer also exposes deterministic sequenced chunk reconstruction for future/other streaming adapters. Chunk evidence contains sequence, length and hash without chunk content.

## Token evidence

Request token limits and Provider response usage are separate facts. Runtime never infers request limits from usage.

If Provider reports completion tokens greater than the sent request limit, Runtime records:

`token_usage_contract_anomaly=true`

This is evidence of a contract/measurement anomaly, not an automatic interpretation of Provider semantics.
