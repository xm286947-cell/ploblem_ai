# OpenAI Mock Test Service 使用说明 V0.1

## 1. 定位

OpenAI Mock Test Service 是统一 Agent Runtime / 编排项目维护的公共测试基础设施。

它用于：
- Runtime 自身的 Provider、Retry、Timeout、Streaming、Parser、Secret、Resume 等高频测试；
- Storage、知识库、重大问题等业务项目通过 Runtime 完成低成本接口联调；
- 稳定复现真实 Provider 不容易主动制造的 429、5xx、延迟、截断、断流等场景。

它不负责模拟模型智能，也不负责定义业务 JSON Schema。

## 2. 启动

在仓库根目录执行：

```bash
python -m tools.openai_mock.server --host 127.0.0.1 --port 8000
```

启动后：

```text
OpenAI Base URL: http://127.0.0.1:8000/v1
Health:          http://127.0.0.1:8000/__mock__/health
```

## 3. 使用原则

调用业务代码只切换 `base_url` 和测试用 `api_key`。

示例：

```python
from openai import OpenAI

client = OpenAI(
    api_key="mock-key",
    base_url="http://127.0.0.1:8000/v1",
    max_retries=0,
)

response = client.responses.create(
    model="mock-gpt",
    input="hello",
)

print(response.output_text)
```

Runtime 场景建议保持 `max_retries=0`，由 Runtime 自己拥有 Retry Budget，避免 SDK 隐式重试干扰测试结论。

## 4. Payload

Payload 完全由测试方构造，Mock 不检查业务结构。

可直接使用：
- 普通文本；
- Markdown；
- 完整 JSON；
- JSON Fragment；
- 超长文本；
- 任意业务内容。

非字符串 JSON 值会被序列化成紧凑 JSON 文本作为模型输出；如果要精确模拟“半截 JSON”，请直接把半截内容作为字符串 Payload。

## 5. 推荐测试分层

开发期优先级：

```text
Runtime 公共能力
    ↓
OpenAI Mock
    ↓
业务项目 + Runtime + Mock
    ↓
少量 Real Provider E2E
```

大部分异常、边界和回归场景应在 Mock 层完成。真实 Provider 用于最终真实性验证，不承担高频可重复性回归。

## 6. 安全

Mock 请求历史不会保存 Bearer Token / API Key 明文。

认证相关信息只保留：
- 是否存在；
- 认证 Scheme；
- 被脱敏后的 Header 值。

不要把生产 API Key 作为 Mock 测试凭据。

## 7. 关闭

前台运行时使用 Ctrl+C 关闭服务。

测试代码也可以通过 `create_server(...)` 创建临时服务，并在测试结束后调用 `shutdown()` / `server_close()`。
