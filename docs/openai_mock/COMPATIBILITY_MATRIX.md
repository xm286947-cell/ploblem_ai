# OpenAI Mock Compatibility Matrix V0.1

Baseline Date: 2026-09-20  
Status: REVIEW_FIX_IN_PROGRESS / V0.1 RC  
Review Basis: PR #17 second review

“兼容”指声明支持的 OpenAI Endpoint 在请求、响应、错误、Streaming 和 SDK 消费层可以被标准调用方式使用；不表示 V0.1 一次性覆盖 OpenAI 全部产品 API，也不表示 Mock 具备真实模型智能。

## 1. OpenAI Compatible Plane

| Endpoint / Capability | V0.1 Status | Python SDK | JS SDK | Runtime / E2E | Notes |
|---|---|---:|---:|---:|---|
| `POST /v1/responses` non-stream | VERIFIED | PASS | PASS | PASS | 标准请求体，无 Mock 专用字段 |
| `POST /v1/responses` stream | VERIFIED | PASS | PASS | PASS | SSE event flow + completed |
| `POST /v1/chat/completions` non-stream | VERIFIED | PASS | PASS | PASS | Runtime 现有 Provider 主链路 |
| `POST /v1/chat/completions` stream | VERIFIED | PASS | PASS | COVERED | data frames + `[DONE]` |
| `GET /v1/models` | VERIFIED | PASS | PASS | N/A | OpenAI-shaped list/model |
| Bearer Authentication | VERIFIED | PASS | PASS | PASS | 缺失/非法认证返回 401 |
| OpenAI-shaped Error Object | VERIFIED | N/A | N/A | PASS | 400/401/429/5xx |
| `x-request-id` | VERIFIED | N/A | N/A | N/A | 每次标准响应生成 |
| `Retry-After` | VERIFIED | N/A | N/A | PASS | 429 可配置 |
| Usage structure | VERIFIED | PASS | PASS | N/A | Responses / Chat 基础 usage |
| Model field propagation | VERIFIED | PASS | PASS | PASS | 返回请求 model |
| Tool / Function Call response construction | NOT IMPLEMENTED | N/A | N/A | N/A | OUT OF V0.1 |
| Other OpenAI endpoints | OUT OF V0.1 | N/A | N/A | N/A | 按 Runtime 实际使用面扩展 |

## 2. Mock Control Plane / Transport Behavior

| Capability | V0.1 Status | Automated Evidence | Notes |
|---|---|---|---|
| `POST /__mock__/scenario` | VERIFIED | HTTP tests | 配置 Payload + behavior |
| `POST /__mock__/reset` | VERIFIED | HTTP tests | 支持全局/单 scenario reset |
| `GET /__mock__/counters` | VERIFIED | HTTP + Runtime | 精确核对真实 HTTP 次数 |
| `GET /__mock__/requests` | VERIFIED | HTTP + Secret tests | 仅保存必要元数据 |
| Arbitrary text payload | VERIFIED | HTTP + Runtime | 不做业务 Schema 校验 |
| JSON payload | VERIFIED | HTTP + Runtime | 序列化为输出文本 |
| JSON fragment | VERIFIED | HTTP + Storage E2E | 支持截断业务输出 |
| Empty payload | VERIFIED | HTTP + Runtime | 支持空结果分支 |
| Long payload | VERIFIED | HTTP + Runtime | 长内容完整往返 |
| `delay_ms` | VERIFIED | HTTP + SDK timeout | 可稳定制造慢响应 |
| `fail_first_n` | VERIFIED | HTTP + Runtime | 前 N 次失败后恢复 |
| `fail_status` | VERIFIED | HTTP + Runtime | 仅允许 400–599 |
| final `status` | FIXED / REVERIFYING | New contract tests | 200–599；2xx 成功路径，3xx–5xx error path |
| non-stream truncate | VERIFIED | HTTP | 指定字节截断 |
| raw invalid body | VERIFIED | HTTP | `raw_response_body` |
| connection drop before response | VERIFIED | HTTP + Runtime | `disconnect_before_response` |
| stream disconnect | VERIFIED | HTTP + Runtime | 部分 delta + 未完成 |
| custom response headers | IMPLEMENTED | HTTP behavior | Control Plane only |
| concurrent scenario isolation | VERIFIED | 40 parallel calls | counters/payload 不串场 |
| Secret redaction | VERIFIED | Mock history + Runtime DB | Authorization/API key 不落明文 |

## 3. Runtime Integration

| Runtime Capability | V0.1 Status | Evidence |
|---|---|---|
| Retry Owner / SDK retry=0 | VERIFIED | Mock counter = Runtime provider_calls |
| 429 → Retry → Success | VERIFIED | real HTTP |
| Persistent 429 → Hard Budget | VERIFIED | real HTTP |
| 500 → Retry → Success | VERIFIED | real HTTP |
| Persistent 503 → Hard Budget | VERIFIED | real HTTP |
| Timeout classification | VERIFIED | real HTTP |
| ConnectionError transport classification | VERIFIED | real HTTP |
| Streaming normal completion | VERIFIED | Runtime streaming case |
| Streaming interrupted | VERIFIED | Runtime streaming case |
| Secret request-time use / no persistence | VERIFIED | Runtime DB + Mock history |
| Crash / Resume / secret re-resolution | VERIFIED | real HTTP + task store |
| Storage JSON truncation recovery | VERIFIED | Runtime + Storage + Mock Golden E2E |

## 4. Official SDK Baseline

| SDK | Acceptance Version | Status | Policy |
|---|---:|---|---|
| OpenAI Python SDK | 3.16.2 | VERIFIED | V0.1 acceptance lane 固定版本 |
| OpenAI JS SDK | 7.20.0 | VERIFIED | V0.1 acceptance lane 固定版本 |

前向兼容策略：后续如需跟踪 SDK 新版本，单独增加 latest-compatible lane；不得让稳定 Acceptance Gate 随依赖自动升级漂移。

## 5. Acceptance Mapping

完整 AC-01 ～ AC-15 与 RT-MOCK-001 ～ RT-MOCK-016 见：

`docs/openai_mock/ACCEPTANCE_REPORT_V0.1.md`

## 6. 维护原则

1. Runtime 新增使用某个 OpenAI 官方 Endpoint 前，先更新本矩阵。
2. 每个新增 Endpoint 必须同时补 Contract Test。
3. Mock Control Plane 不得污染 `/v1` 请求 Schema。
4. SDK 升级后至少重新跑 Python + JS SDK Compatibility Gate。
5. Runtime Retry / Budget 等公共能力优先通过 Runtime → Mock real HTTP gate 验证，不只依赖内存 Stub。
6. 对未验证能力明确标记 `NOT VERIFIED` / `NOT IMPLEMENTED` / `OUT OF V0.1`，不以“OpenAI-compatible”泛化替代测试证据。
7. 本矩阵必须保持“能力 × 状态 × 证据”结构，不得退化为单纯验收摘要。
