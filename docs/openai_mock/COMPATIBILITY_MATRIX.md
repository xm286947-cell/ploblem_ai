# OpenAI Mock Compatibility Matrix V0.1

Baseline Date: 2026-09-20  
Status: DEVELOPMENT

“兼容”指声明支持的 OpenAI Endpoint 在请求/响应/Streaming/Error 等协议层可以被标准调用方式消费；不表示 V0.1 一次性覆盖 OpenAI 全部产品 API，也不表示 Mock 具备真实模型智能。

| 能力 | V0.1 状态 | 自动化验证 |
|---|---|---|
| `POST /v1/responses` 非流式 | IMPLEMENTED | `test_openai_mock_server.py` |
| `POST /v1/responses` Streaming SSE | IMPLEMENTED | HTTP + Python SDK tests |
| `POST /v1/chat/completions` 非流式 | IMPLEMENTED | HTTP + Python SDK tests |
| `POST /v1/chat/completions` Streaming | IMPLEMENTED | HTTP + Python SDK tests |
| `GET /v1/models` | IMPLEMENTED | HTTP + Python SDK tests |
| Bearer Authentication | IMPLEMENTED | HTTP tests |
| OpenAI-shaped Error Object | IMPLEMENTED | HTTP tests |
| 429 / 500 / 503 | IMPLEMENTED | scenario + contract tests |
| `Retry-After` | IMPLEMENTED | scenario support |
| `x-request-id` | IMPLEMENTED | protocol response |
| 任意文本 Payload | IMPLEMENTED | HTTP tests |
| JSON Payload 序列化为输出文本 | IMPLEMENTED | HTTP tests |
| JSON Fragment / 碎片文本 | IMPLEMENTED | HTTP tests |
| 延迟 / Timeout 前置条件 | IMPLEMENTED | HTTP tests |
| 非流式 Body 截断 | IMPLEMENTED | HTTP tests |
| Streaming 中途断连 | IMPLEMENTED | HTTP tests |
| fail-first-N | IMPLEMENTED | HTTP tests |
| 请求计数 | IMPLEMENTED | HTTP tests |
| Secret 脱敏请求历史 | IMPLEMENTED | HTTP tests |
| OpenAI Python SDK | CI VERIFYING | `test_openai_mock_sdk_compat.py` |
| OpenAI JS/TS SDK | NOT VERIFIED | 后续验收补充 |
| Tool / Function Call 响应构造 | NOT IMPLEMENTED | Runtime 实际需要时纳入 |
| OpenAI 其他 Endpoint | OUT OF V0.1 | 按 Runtime 使用面扩展 |

## 兼容维护原则

1. Runtime 新增使用某个 OpenAI 官方 Endpoint 前，先更新本矩阵。
2. 每个新增 Endpoint 必须同时补 Contract Test。
3. Mock Control Plane 不得污染 `/v1` 请求 Schema。
4. SDK 升级后至少重新跑 Python SDK Compatibility Gate。
5. 对未验证能力明确标记 NOT VERIFIED，不以“OpenAI-compatible”泛化替代测试证据。
