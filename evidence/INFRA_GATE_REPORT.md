# Public Knowledge RAG infrastructure gate report

Date: 2026-10-04
Task: `PUBLIC-KNOWLEDGE-RAG-INFRASTRUCTURE-001` / GitHub #385

## Result

`RESULT=PARTIAL`
`INFRA_GATE=NOT_PASS`

All service and provider gates below passed. `NEXTCLOUD_DELIVERY` remains blocked because this Mac has no Nextcloud client, mounted Nextcloud folder, configured destination, or available Nextcloud connector. The package is being prepared locally; it is not represented as delivered to Nextcloud. #387's frozen-source import and 60-query Golden run were not started.

## Runtime

- Host: macOS 27.0.1, Apple Silicon arm64, 18 GiB physical memory.
- Container runtime: Docker Desktop 4.93.0; Docker Engine 29.8.1; Docker Compose 5.5.1.
- Service: healthy at `http://127.0.0.1:8080`; Compose limits the service container to 4 GiB and four CPUs.
- API image: 270,259,075 bytes. Public knowledge volume after synthetic smoke: 108 KiB; Docker reports the local volume as 102.5 KiB.
- Service cgroup memory during/after smoke: 56,070,144 bytes current; 63,201,280 bytes peak. Docker stats reported 44.52 MiB current. This is below the 4 GiB service cap; remote model memory is excluded.

## Gate results

| Gate | Result | Evidence |
|---|---|---|
| `MAC_RUNTIME` / API boot | PASS | Compose container healthy; `/health` returned `ok` |
| `PUBLIC_SOURCE_GATE` | PASS | PUBLIC-only, private IP/URL, credential URL, and outbound private-context tests |
| `SOURCE_VERSIONING` | PASS | Idempotent import and changed-content revision tests |
| `PARSER_INTERFACE` | PASS | Replaceable parser contract and text bootstrap adapter exercised |
| `RETRIEVAL_INTERFACE` | PASS | Replaceable retriever contract and container search exercised |
| `OLLAMA_REMOTE_CONNECTIVITY` | PASS | `http://192.168.1.100:11434`, Ollama `0.23.0` |
| `OLLAMA_REMOTE_MODEL_LIST` | PASS | Exact tag and digest matched the live `/api/tags` response |
| `OLLAMA_REMOTE_INFERENCE` / test provider | PASS | One short generic probe returned non-empty output |
| `OLLAMA_REMOTE_TIMEOUT_FAIL_CLOSED` | PASS | Unreachable-provider container returned HTTP 503 and no answer |
| `MANUAL_PROVIDER_ADAPTER_SLOT` | PASS | Explicit unconfigured slot; fail-closed policy visible |
| Embedding/index/citation adapter slots | PASS | Contracts are replaceable; no final component is selected here |
| `LIVE_API` | PASS | Container imported synthetic PUBLIC text and returned a model answer |
| `FIXTURE_CAPTURE` / `FIXTURE_REPLAY` | PASS | Captured LIVE answer, verified SHA, replayed same answer without a provider call |
| `CITATION_CONTRACT` | PASS | `source_id`, revision, locator, text resolved by citation endpoint |
| `CONFIG_HASH` | PASS | Stable 64-character SHA-256 from `/config` |
| `SECRET_ISOLATION` | PASS | No `.env` tracked; policy tests reject credential patterns; no credentials in evidence |
| Fresh package extraction smoke | PASS | ZIP CRC and 33 manifest file hashes verified; extracted Compose service booted healthy; LIVE/citation/fixture and 503 fail-closed smokes passed |
| `NEXTCLOUD_DELIVERY` | BLOCKED | No configured Nextcloud destination or client/connector on this Mac |

Automated tests: 6 passed. The only test warning is Starlette's deprecation notice recommending `httpx2` for `TestClient`; no test failed.

## Model probe

- Endpoint: `http://192.168.1.100:11434`
- Ollama version: `0.23.0`
- Measured available Qwen tag: `qwen3-vl:8b-thinking-q4_K_M`
- Digest: `901cae73216286ea8c5aba8b46d307ff7188f737285ec500c795a12f05225d28`
- Inference probe: one generic prompt, 15 input tokens, 58 output tokens, 1.03 seconds.
- The service also passed an end-to-end LIVE answer with 64 prompt tokens and 164 output tokens. No internal or frozen Golden material was sent.
- Remote host CPU/GPU/model-memory telemetry is unavailable through the Ollama API and was not inferred. The tag/digest pin here proves provider connectivity for #385; final #387 model selection remains governed by its own task.

## Scope boundary

Only a synthetic public smoke note was imported. The 21-source set, 60 Frozen Golden queries, citation-supported-rate quality gate, and embedding/parser/vector/retrieval selection were left to the authorized #387 stage. Storage product source and formal data were not changed.

## Next action

Configure or mount the approved Nextcloud destination, then deliver the already-built package and verify its SHA there. Until that delivery gate is verified, report #385 as partial and leave #387 stopped.
