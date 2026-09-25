# Storage Runtime S-A02 — Provider Adapter Boundary

Status: PASS (adapter-level)
Date: 2026-09-20
Scope: Runtime-managed Storage path only

## Frozen boundary

- One Runtime Provider Attempt = one real Provider HTTP request.
- Storage Runtime Adapter does not retry.
- `sdk_retry=0` is mandatory; non-zero is rejected before any request.
- Adapter does not read `config/agent.yaml`, `DASHSCOPE_API_KEY`, `ZHIPU_API_KEY`, or Storage provider env configuration.
- Resolved provider/model/secret parameters come only from `context.runtime.provider_config` supplied by Unified Runtime / Agent Config.
- Runtime remains the sole Retry Owner.

## Error mapping

- timeout/network/429/408/425/5xx -> `TRANSPORT`, retryable=true
- `finish_reason=length`, invalid/empty structured JSON -> `VALIDATION`, retryable=true
- auth/bad request/unsupported provider/invalid adapter payload -> `EXECUTION`, retryable=false
- no internal transport retry
- no internal validation retry

## Legacy boundary

`storage_life.ai` retains existing local-direct compatibility behavior for the frozen RC3 product path.  Its historic chunk/final-review retry logic MUST NOT be registered as a Runtime-managed handler.

The Runtime-managed path is `storage_life.runtime_provider_adapter` and is intentionally isolated from legacy config/retry logic.

## Verification

Automated tests prove:

1. one adapter invocation creates exactly one HTTP request;
2. truncation emits one Runtime validation error with no second request;
3. 429 emits one Runtime transport error with no second request;
4. non-retryable 4xx does not request again;
5. invalid JSON emits validation error with no internal retry;
6. `sdk_retry != 0` fails before Provider call;
7. Runtime-managed path does not resolve Storage local provider config/secrets;
8. two simulated Runtime Provider Attempts produce exactly two real HTTP requests.

Actual `AgentResult.execution.provider_calls` end-to-end reconciliation against a live Unified Runtime instance remains an S-A04 Acceptance Pack case; S-A02 establishes the necessary one-attempt/one-request invariant.
