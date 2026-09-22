# RUNTIME-SEMANTIC-HANDOFF-001

Status: DEVELOPMENT / Storage integration candidate

## Contract

Runtime remains fail-closed. If strict JSON parsing fails and deterministic wrapper recovery cannot produce one complete unambiguous JSON value, Runtime returns:

```text
SEMANTIC_REPAIR_REQUIRED
```

Runtime does not repair business semantics, infer missing fields, call a Storage Agent, or use a loose/regex JSON parser.

## Standard failure context

Example:

```json
{
  "code": "SEMANTIC_REPAIR_REQUIRED",
  "category": "VALIDATION",
  "details": {
    "recoverable_content_available": true,
    "content_ref": "semantic-handoff:<opaque-id>",
    "content_hash": "<sha256>",
    "content_length": 1287,
    "content_access_scope": "TASK",
    "raw_finish_reason": "stop",
    "raw_usage": {
      "prompt_tokens": 3048,
      "completion_tokens": 70012,
      "total_tokens": 73008
    },
    "request_max_tokens": 8192,
    "request_max_completion_tokens": "NOT_SENT",
    "structured_output_capability": "UNSUPPORTED",
    "structured_output_request": "NONE",
    "response_format_type": "NOT_SENT"
  }
}
```

The failure context never contains full Provider content.

## Controlled content access

The full Provider content is stored only as a task-scoped semantic handoff attachment. It is not written to ordinary Provider diagnostics, Runtime result JSON, Attempt JSON, Prompt logs, or error details.

Business code may explicitly retrieve it through Runtime:

```python
result = runtime.invoke(request)
if (
    result.error
    and result.error.code == "SEMANTIC_REPAIR_REQUIRED"
    and result.error.details.get("recoverable_content_available")
):
    content = runtime.read_semantic_handoff_content(
        task_id=result.task_id,
        content_ref=result.error.details["content_ref"],
    )
```

Both `task_id` and `content_ref` are required. A reference from another task cannot be read through this API.

## Retry semantics

Every semantic failure Attempt gets its own `AttemptRecord.raw_response_ref`.

If Runtime performs validation retries, the final error context keeps the content reference and evidence of the **first semantic Provider response for that step**. This prevents the first returned information from being overwritten by later retry output.

## Security boundary

The semantic attachment contains only the Provider-returned content required for controlled downstream analysis. Runtime does not separately store or log:

- Prompt text
- source PDF content
- API keys
- Authorization headers

Provider content is not emitted to ordinary logs or diagnostics.

## Mock contract matrix

Required scenarios:

1. strict JSON -> PASS
2. Markdown wrapper -> deterministic recovery -> PASS
3. explanation + single JSON -> deterministic recovery -> PASS
4. truncated JSON -> SEMANTIC_REPAIR_REQUIRED
5. broken JSON -> SEMANTIC_REPAIR_REQUIRED
6. multi JSON -> SEMANTIC_REPAIR_REQUIRED
7. useful-but-invalid-json -> SEMANTIC_REPAIR_REQUIRED + controlled content_ref

All semantic-damage cases remain fail-closed.
