# OpenAI Mock Test Service 使用说明 V0.1

## 1. 定位

OpenAI Mock Test Service 是统一 Agent Runtime / 编排项目维护的公共测试基础设施。

它用于：
- Runtime 自身的 Provider、Retry、Timeout、Streaming、Parser、Secret、Resume 等高频测试；
- Storage、知识库、重大问题等业务项目通过 Runtime 完成低成本接口联调；
- 稳定复现真实 Provider 不容易主动制造的 429、5xx、延迟、截断、断流等场景。

它不负责模拟模型智能，也不负责定义业务 JSON Schema。

## 2. 安装与启动

Mock Service 本体只使用 Python 标准库，不要求额外安装 Web Framework。

如需运行完整兼容性与验收测试，在仓库根目录执行：

```bash
python -m pip install -r requirements-openai-mock-test.txt
python -m pip install -r requirements-runtime-p0-test.txt
```

V0.1 稳定 Acceptance SDK 基线：
- OpenAI Python SDK：`3.16.2`；
- OpenAI JS SDK：`7.20.0`。

稳定验收版本固定；如需跟踪未来 SDK 版本，使用独立 latest-compatible 检查，不改变 V0.1 Acceptance Gate 的可重复性。

启动 Mock Service：



```bash
python -m tools.openai_mock.server --host 127.0.0.1 --port 8000
```

启动后：

```text
OpenAI Base URL: http://127.0.0.1:8000/v1
Health:          http://127.0.0.1:8000/__mock__/health
```

## 3. Mock / Real Provider 切换

业务调用代码不因为 Mock 改写请求结构，只切换 Provider 配置。

Mock：

```text
base_url = http://127.0.0.1:8000/v1
api_key = mock-key
```

Real Provider：

```text
base_url = <真实 Provider OpenAI-compatible Base URL>
api_key = <由环境变量/Secret 管理提供>
```

推荐把两组配置放在不同的 model/profile 配置中，由测试环境选择 profile，而不是在业务代码里写 Mock 判断。

## 4. 使用原则

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

## 5. Payload

Payload 完全由测试方构造，Mock 不检查业务结构。

可直接使用：
- 普通文本；
- Markdown；
- 完整 JSON；
- JSON Fragment；
- 超长文本；
- 任意业务内容。

非字符串 JSON 值会被序列化成紧凑 JSON 文本作为模型输出；如果要精确模拟“半截 JSON”，请直接把半截内容作为字符串 Payload。

## 6. 推荐测试分层

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

## 7. 什么时候用 Mock / 什么时候必须用 Real Provider

优先使用 Mock：
- Retry / Retry Budget；
- Timeout；
- 429 / 5xx；
- JSON 截断；
- Streaming / 断流；
- Secret 持久化；
- Resume / Crash Recovery；
- Parser 边界与稳定回归。

必须保留 Real Provider E2E：
- Provider 实际鉴权与网络连通；
- 官方/厂商真实协议差异；
- 模型真实输出质量；
- Token、模型能力和供应商侧限制；
- 发布前少量关键真实性验证。

## 8. 安全

Mock 请求历史不会保存 Bearer Token / API Key 明文。

认证相关信息只保留：
- 是否存在；
- 认证 Scheme；
- 被脱敏后的 Header 值。

不要把生产 API Key 作为 Mock 测试凭据。

## 9. 关闭

前台运行时使用 Ctrl+C 关闭服务。

测试代码也可以通过 `create_server(...)` 创建临时服务，并在测试结束后调用 `shutdown()` / `server_close()`。
