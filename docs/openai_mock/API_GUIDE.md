# OpenAI Mock Test Service 调用说明 V0.1

## 1. OpenAI Compatible Plane

V0.1 声明支持：

```text
POST /v1/responses
POST /v1/chat/completions
GET  /v1/models
```

`/v1/...` 接口不得加入 Mock 专用请求字段。

调用方使用标准 OpenAI 请求；Mock 行为通过独立 Control Plane 配置。

所有 `/v1/...` 请求要求：

```http
Authorization: Bearer <test-key>
```

## 2. Mock Control Plane

### 2.1 配置 Scenario

```http
POST /__mock__/scenario
Content-Type: application/json
```

示例：

```json
{
  "scenario_key": "runtime-retry-001",
  "payload": "{\"result\":\"ok\"}",
  "behavior": {
    "fail_first_n": 2,
    "fail_status": 429,
    "retry_after": "0",
    "chunk_size": 16
  }
}
```

### 2.2 Behavior 字段

| 字段 | 含义 |
|---|---|
| `status` | 正常阶段最终 HTTP Status，默认 200 |
| `delay_ms` | 返回前延迟毫秒数 |
| `stream` | 可选；强制覆盖请求中的 stream |
| `fail_first_n` | 前 N 次请求失败 |
| `fail_status` | 前 N 次失败时的 HTTP Status |
| `retry_after` | 错误响应中的 Retry-After |
| `truncate_at` | 非流式响应在指定字节处截断 |
| `disconnect_at` | 流式响应发送指定字节后断开 |
| `chunk_size` | 流式文本 delta 的分块大小 |
| `headers` | 附加响应 Header |
| `raw_response_body` | 直接返回原始 Body，用于协议异常测试 |

## 3. Scenario 选择

默认场景键：

```text
default
```

并发测试可使用测试专用 Header：

```http
X-Mock-Scenario-Key: runtime-retry-001
```

该 Header 只用于 Mock 测试环境，不进入 OpenAI 请求 Body，因此不会污染 OpenAI Schema。

示例：

```bash
curl http://127.0.0.1:8000/v1/responses \
  -H "Authorization: Bearer mock-key" \
  -H "Content-Type: application/json" \
  -H "X-Mock-Scenario-Key: runtime-retry-001" \
  -d '{"model":"mock-gpt","input":"hello"}'
```

## 4. 查询调用次数

```http
GET /__mock__/counters
```

返回示例：

```json
{
  "object": "mock.counters",
  "data": {
    "runtime-retry-001": 3
  }
}
```

它用于验证：
- Runtime 实际发起了多少次 HTTP 请求；
- SDK 是否产生了隐式 Retry；
- Retry Budget 是否与预期一致。

## 5. 查询请求记录

全部：

```http
GET /__mock__/requests
```

指定 Scenario：

```http
GET /__mock__/requests?scenario_key=runtime-retry-001
```

请求记录只保存必要元数据，Secret Header 值会被替换为 `[REDACTED]`。

## 6. Reset

全部清空：

```http
POST /__mock__/reset
Content-Type: application/json

{}
```

只清理一个 Scenario：

```json
{
  "scenario_key": "runtime-retry-001"
}
```

## 7. 常见场景

前 2 次 429，第 3 次成功：

```json
{
  "scenario_key": "retry",
  "payload": "recovered",
  "behavior": {
    "fail_first_n": 2,
    "fail_status": 429
  }
}
```

超时/慢响应：

```json
{
  "scenario_key": "slow",
  "payload": "late",
  "behavior": {
    "delay_ms": 5000
  }
}
```

截断 JSON：

```json
{
  "scenario_key": "truncated",
  "payload": "{\"answer\":\"incomplete",
  "behavior": {}
}
```

协议级非法 Body：

```json
{
  "scenario_key": "bad-body",
  "payload": "ignored",
  "behavior": {
    "raw_response_body": "not-json-at-all"
  }
}
```

Streaming 中途断连：

```json
{
  "scenario_key": "disconnect",
  "payload": "very long content ...",
  "behavior": {
    "chunk_size": 10,
    "disconnect_at": 200
  }
}
```
