# OpenAI Mock Compatibility Matrix V0.1

Baseline Date: 2026-09-20  
Status: V0.1 RC / CORE GATES PASS  
Evidence: GitHub Actions Run #198 — 35512386719

“兼容”指声明支持的 OpenAI Endpoint 在请求/响应/Streaming/Error 等协议层可以被标准调用方式消费；不表示 V0.1 一次性覆盖 OpenAI 全部产品 API，也不表示 Mock 具备真实模型智能。

| 能力 | V0.1 状态 | 自动化验证 |
|---|---|---|
| `POST /v1/responses` 非流式 | VERIFIED | HTTP + official Python/JS SDK |
| `POST /v1/responses` Streaming SSE | VERIFIED | HTTP + official Python/JS SDK |
| `POST /v1/chat/completions` 非流式 | VERIFIED | HTTP + official Python/JS SDK |
| `POST /v1/chat/completions` Streaming | VERIFIED | HTTP + official Python/JS SDK |
| `GET /v1/models` | VERIFIED | HTTP + official Python/JS SDK |
| Bearer Authentication | VERIFIED | HTTP tests |
| OpenAI-shaped Error Object | VERIFIED | HTTP tests |
| 429 / 500 / 503 | VERIFIED | scenario tests + Runtime real-HTTP integration |
| `Retry-After` | VERIFIED | scenario support + 429 integration |
| `x-request-id` | VERIFIED | protocol response |
| 任意文本 Payload | VERIFIED | HTTP tests |
| JSON Payload 序列化为输出文本 | VERIFIED | HTTP tests |
| JSON Fragment / 碎片文本 | VERIFIED | HTTP tests |
| 延迟 / Timeout 前置条件 | VERIFIED | HTTP tests |
| 非流式 Body 截断 | VERIFIED | HTTP tests |
| Streaming 中途断连 | VERIFIED | HTTP tests |
| fail-first-N | VERIFIED | HTTP + Runtime integration |
| 请求计数 | VERIFIED | HTTP + Runtime integration |
| Secret 脱敏请求历史 | VERIFIED | HTTP tests |
| OpenAI Python SDK | VERIFIED | openai 3.16.2 / 5 SDK cases in CI |
| OpenAI JS/TS SDK | VERIFIED | openai 7.20.0 / 5 SDK cases in CI |
| Runtime → Mock 真实 HTTP 429 恢复 | VERIFIED | `test_openai_mock_runtime_integration.py` |
| Runtime → Mock 真实 HTTP 500 恢复 | VERIFIED | `test_openai_mock_runtime_integration.py` |
| Runtime → Mock 真实 HTTP 503 + Hard Budget | VERIFIED | `test_openai_mock_runtime_integration.py` |
| Tool / Function Call 响应构造 | NOT IMPLEMENTED | Runtime 实际需要时纳入 |
| OpenAI 其他 Endpoint | OUT OF V0.1 | 按 Runtime 使用面扩展 |

## V0.1 Gate Evidence

GitHub Actions Run #198：

- Mock HTTP + official Python SDK：18 passed；
- Runtime → Mock real HTTP integration：3 passed；
- official OpenAI JS SDK：5/5 PASS；
- Runtime P0 + Storage integration regression：119 passed；
- 两个 CI job 均 PASS。

Runtime-to-Mock 集成用例不是内存 Stub：使用现有 `OpenAICompatibleClient`、`LegacyQualityIssueStageHandlerAdapter`、`LightweightExecutionEngine` 通过真实本地 HTTP 调用 Mock，用 Mock counter 对实际 Provider HTTP 请求次数做独立校验。

## 兼容维护原则

1. Runtime 新增使用某个 OpenAI 官方 Endpoint 前，先更新本矩阵。
2. 每个新增 Endpoint 必须同时补 Contract Test。
3. Mock Control Plane 不得污染 `/v1` 请求 Schema。
4. SDK 升级后至少重新跑 Python + JS SDK Compatibility Gate。
5. Runtime Retry / Budget 等公共能力优先通过 Runtime → Mock real HTTP gate 验证，不只依赖内存 Stub。
6. 对未验证能力明确标记 NOT VERIFIED，不以“OpenAI-compatible”泛化替代测试证据。
