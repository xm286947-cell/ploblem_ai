# OpenAI Mock Test Service V0.1 验收报告

日期：2026-09-20  
状态：ACCEPTANCE_PASS / V0.1 RC / READY_FOR_REVIEW  
范围：OPENAI-MOCK-001  
Issue：#16  
Draft PR：#17  
当前分支 Head：`c29b567c0690ed5a774a67ce3c1ab4789a0e3b01`\n验收执行代码 Head：`0e02157b667119d77cf812992fe92cf12a297c78`  
最终验收 CI：GitHub Actions Run #240 / `35516985444` — PASS

> 本报告表示 V0.1 已按冻结设计基线完成自动化验收。当前仍未合并 main，不标记 DONE。

## 1. 最终 Gate

| Gate | 结果 | 证据 |
|---|---|---|
| Runtime P0 + Storage 历史回归 | PASS | 119 passed |
| OpenAI Mock V0.1 全量 Python Acceptance | PASS | 38 passed |
| Runtime → Mock real HTTP / Secret / Resume / Streaming / Storage E2E | PASS | 15 cases（包含于 38 passed） |
| OpenAI JS SDK | PASS | 5/5 PASS |
| PR/Branch CI | PASS | Run 35516985444 |
| Secret Persistence | PASS | Runtime DB + Mock request history 双侧验证 |
| Retry Owner / Hard Budget | PASS | Mock counters 与 Runtime provider_calls 对齐 |
| Storage Runtime+Mock E2E | PASS | JSON 截断 → Runtime Retry → Golden PASS |

## 2. AC-01 ～ AC-15

| AC | 验收要求 | 结果 | 主要证据 |
|---|---|---|---|
| AC-01 | Python SDK 仅切换 base_url/api_key 即可调用 | PASS | `test_openai_mock_sdk_compat.py` 默认 scenario，无 Mock 专用请求字段/Header |
| AC-02 | JS/TS SDK 仅切换 base_url/api_key 即可调用 | PASS | `openai_mock_sdk_compat.mjs` 默认 Responses 调用 |
| AC-03 | `/v1` 不出现 Mock 专用请求字段 | PASS | HTTP contract tests + Control Plane 独立 `/__mock__/` |
| AC-04 | Responses 非流式兼容 | PASS | HTTP + Python SDK + JS SDK |
| AC-05 | Chat Completions 非流式兼容 | PASS | HTTP + Python SDK + JS SDK |
| AC-06 | Streaming SSE 兼容 | PASS | Responses / Chat streaming；Runtime streaming 完成与断流场景 |
| AC-07 | 429/500/503/Timeout 可稳定复现 | PASS | real HTTP Runtime integration + SDK timeout |
| AC-08 | fail-first-N 行为确定 | PASS | 前 N 次失败、后续成功 + exact counter |
| AC-09 | counters 精确反映 HTTP 调用次数 | PASS | 429/500/503/Storage/Resume 多组独立核对 |
| AC-10 | 任意文本/JSON/Fragment，不承担业务 Schema | PASS | text、JSON、JSON fragment、empty、long payload |
| AC-11 | Mock 请求历史不保存真实 Secret | PASS | Authorization 值 REDACTED；原 Secret 不存在于 history |
| AC-12 | Runtime 第一批公共场景全部自动化 | PASS | RT-MOCK-001 ～ RT-MOCK-016 全覆盖，见下表 |
| AC-13 | Storage 可通过 Runtime+Mock 完成 E2E | PASS | `test_openai_mock_storage_e2e.py` |
| AC-14 | 使用说明/API说明/兼容矩阵完整 | PASS | `docs/openai_mock/` + examples |
| AC-15 | Mock 测试不依赖 Codex | PASS | GitHub Actions 独立执行全部 Gate |

## 3. RT-MOCK-001 ～ RT-MOCK-016

| Case | 场景 | 结果 | 自动化证据 |
|---|---|---|---|
| RT-MOCK-001 | 正常文本返回 | PASS | `test_rt_mock_001_runtime_normal_text_round_trips` |
| RT-MOCK-002 | 正常结构化 Payload | PASS | `test_rt_mock_002_runtime_structured_payload_semantics_are_unchanged` |
| RT-MOCK-003 | Streaming 正常完成 | PASS | `test_rt_mock_003_runtime_streaming_completes_in_order` |
| RT-MOCK-004 | 429 后恢复 | PASS | 429×2 → 第3次成功；provider_calls=3 |
| RT-MOCK-005 | 持续 429 | PASS | Hard Budget 后确定性失败 |
| RT-MOCK-006 | 500 / 503 | PASS | 500 恢复 + 持续503 Hard Budget |
| RT-MOCK-007 | Timeout | PASS | SDK deterministic timeout + Runtime transport classification |
| RT-MOCK-008 | 连接中断 | PASS | `disconnect_before_response` + Runtime transport retry |
| RT-MOCK-009 | Streaming 中途断开 | PASS | 部分 delta 可观察，未收到 completed 时失败 |
| RT-MOCK-010 | 内容截断 | PASS | Storage JSON Fragment → Runtime validation retry → 完整结果 |
| RT-MOCK-011 | 空响应 | PASS | 空结果不误判成功 |
| RT-MOCK-012 | 超长 Payload | PASS | Runtime real HTTP 长内容完整往返 |
| RT-MOCK-013 | Retry Owner | PASS | SDK retry=0；Mock counter = Runtime provider_calls |
| RT-MOCK-014 | Secret Persistence | PASS | Secret 请求时可用；Runtime DB 与 Mock history 均无明文 |
| RT-MOCK-015 | Resume | PASS | Crash 后新 Run Resume；重新解析 Secret；只产生一个最终 Commit |
| RT-MOCK-016 | 并发隔离 | PASS | 40 并发请求，A/B 各20次，Payload/counter 不串场 |

## 4. 验收过程中发现并修复的问题

### A01 Connection Drop 只覆盖 Responses

验收新增 RT-MOCK-008 后发现：`disconnect_before_response` 最初只对 `/v1/responses` 生效，而现有 `OpenAICompatibleClient` 使用 `/v1/chat/completions`。

处理：补齐 Chat Completions 的 connection drop 行为。

### A02 Provider Boundary 未将 RemoteDisconnected 归类为 Transport

补齐 Mock 后继续暴露真实 Runtime/Provider 边界问题：TCP/HTTP 连接被服务端直接断开时，`RemoteDisconnected` 未进入 `AIClientError`，导致 Runtime 将其视为不可重试普通异常。

处理：`builder/ai_client.py` 增加 `ConnectionError` 捕获，使连接中断统一进入 transport failure，由 Runtime Retry Policy / Retry Budget 管理。

修复后 RT-MOCK-008 从：
- FAILED，provider_calls=1，retryable=false

变为：
- Runtime 按 transport policy 执行 2 次真实 HTTP；
- Mock counter=2；
- Hard Budget 后确定性失败；
- 最终 Gate PASS。

这说明 Mock 已实际发挥“稳定暴露 Runtime Provider Boundary 缺陷”的价值，而不仅是返回固定假数据。

### A03 CI 依赖顺序

曾将全部 `test_openai_mock*.py` 放在仅安装 Mock SDK 依赖后的步骤执行，导致 Runtime 集成测试缺少 PyYAML 等依赖。

处理：恢复分层 Gate：
1. Mock HTTP + Python SDK；
2. 安装 Runtime test dependencies；
3. Runtime → Mock integration；
4. JS SDK compatibility。

最终 Run #226 全绿。

## 5. 交付物

- `tools/openai_mock/server.py`
- `tools/openai_mock/__init__.py`
- `tools/openai_mock/examples/python_sdk_example.py`
- `tools/openai_mock/examples/curl_example.sh`
- `tools/openai_mock/examples/business_integration_example.py`
- `tests/test_openai_mock_server.py`
- `tests/test_openai_mock_sdk_compat.py`
- `tests/test_openai_mock_runtime_integration.py`
- `tests/test_openai_mock_runtime_streaming.py`
- `tests/test_openai_mock_runtime_secret_resume.py`
- `tests/test_openai_mock_storage_e2e.py`
- `tests/openai_mock_sdk_compat.mjs`
- `docs/openai_mock/USER_GUIDE.md`
- `docs/openai_mock/API_GUIDE.md`
- `docs/openai_mock/COMPATIBILITY_MATRIX.md`
- `requirements-openai-mock-test.txt`
- CI Gate：`.github/workflows/agent-runtime-p0.yml`

## 6. 当前结论

V0.1 冻结设计基线的 AC-01 ～ AC-15：**15 / 15 PASS**。  
RT-MOCK-001 ～ RT-MOCK-016：**16 / 16 PASS**。  
Runtime 历史回归：**PASS**。

当前状态推进为：

```text
ACCEPTANCE_PASS / V0.1 RC / READY_FOR_REVIEW
```

仍保持：
- PR #17 已进入 Ready for Review；
- 未合并 main；
- 不标记 DONE。

后续仅需做 Review/合并决策，不需要继续扩展 V0.1 Scope。
