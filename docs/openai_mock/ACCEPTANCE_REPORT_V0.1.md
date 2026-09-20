# OpenAI Mock Test Service V0.1 验收报告

日期：2026-09-20  
状态：REVIEW_PASS / V0.1 RC / READY_FOR_MERGE_DECISION  
范围：OPENAI-MOCK-001  
Issue：#16  
PR：#17 / Ready for Review  
Review-fix 可执行代码 Head：`fc4feae63a61d361d00faa377d7409d3d10811a7`  
Review-fix Gate：GitHub Actions Run #256 / `35517950221` — PASS

> 本报告表示 V0.1 已按冻结设计基线完成验收，并完成二次独立 Review 的阻断项修复。当前仍未合并 main，不标记 DONE。

## 1. 最终 Gate

| Gate | 结果 | 证据 |
|---|---|---|
| Runtime P0 + Storage 历史回归 | PASS | 119 passed |
| OpenAI Mock V0.1 全量 Python Acceptance | PASS | 43 passed |
| OpenAI JS SDK | PASS | 5/5 PASS |
| Review blocker fix gate | PASS | Run #256 / 35517950221 |
| Secret Persistence | PASS | Runtime DB + Mock request history 双侧验证 |
| Retry Owner / Hard Budget | PASS | Mock counters 与 Runtime provider_calls 对齐 |
| Storage Runtime+Mock E2E | PASS | JSON 截断 → Runtime Retry → Golden PASS |
| Final HTTP status contract | PASS | 429→202、stream 206、models 203、非法状态校验 |
| Compatibility Matrix | PASS | 恢复能力 × 状态 × 证据矩阵 |

## 2. AC-01 ～ AC-15

| AC | 验收要求 | 结果 | 主要证据 |
|---|---|---|---|
| AC-01 | Python SDK 仅切换 base_url/api_key 即可调用 | PASS | official Python SDK |
| AC-02 | JS/TS SDK 仅切换 base_url/api_key 即可调用 | PASS | official JS SDK |
| AC-03 | `/v1` 不出现 Mock 专用请求字段 | PASS | Control Plane 独立 `/__mock__/` |
| AC-04 | Responses API 普通请求通过 | PASS | HTTP + Python/JS SDK |
| AC-05 | Chat Completions 普通请求通过 | PASS | HTTP + Python/JS SDK |
| AC-06 | Streaming SSE 兼容测试通过 | PASS | Responses / Chat streaming + Runtime |
| AC-07 | 429/500/503/Timeout 可稳定复现 | PASS | Runtime real HTTP + SDK timeout |
| AC-08 | fail_first_n 行为可确定性复现 | PASS | exact counter |
| AC-09 | counters 精确反映真实 HTTP 请求次数 | PASS | Runtime provider_calls 对齐 |
| AC-10 | 任意文本/JSON/Fragment，不承担业务 Schema | PASS | text/JSON/fragment/empty/long |
| AC-11 | 请求历史不得保存真实 Secret | PASS | Authorization/API key redaction |
| AC-12 | Runtime 第一批公共场景全部自动化 | PASS | RT-MOCK-001～016 |
| AC-13 | Storage 可通过 Runtime+Mock 完成 E2E | PASS | Storage Golden E2E |
| AC-14 | 使用说明/API说明/兼容矩阵完整 | PASS | `docs/openai_mock/` |
| AC-15 | Mock 测试不依赖 Codex | PASS | GitHub Actions 独立执行 |

## 3. RT-MOCK-001 ～ RT-MOCK-016

| Case | 场景 | 结果 |
|---|---|---|
| RT-MOCK-001 | 正常文本返回 | PASS |
| RT-MOCK-002 | 正常结构化 Payload | PASS |
| RT-MOCK-003 | Streaming 正常完成 | PASS |
| RT-MOCK-004 | 429 后恢复 | PASS |
| RT-MOCK-005 | 持续 429 | PASS |
| RT-MOCK-006 | 500 / 503 | PASS |
| RT-MOCK-007 | Timeout | PASS |
| RT-MOCK-008 | 连接中断 | PASS |
| RT-MOCK-009 | Streaming 中途断开 | PASS |
| RT-MOCK-010 | 内容截断 | PASS |
| RT-MOCK-011 | 空响应 | PASS |
| RT-MOCK-012 | 超长 Payload | PASS |
| RT-MOCK-013 | Retry Owner | PASS |
| RT-MOCK-014 | Secret Persistence | PASS |
| RT-MOCK-015 | Resume | PASS |
| RT-MOCK-016 | 并发隔离 | PASS |

## 4. 验收阶段已修复问题

### A01 Connection Drop 只覆盖 Responses

最初 `disconnect_before_response` 只覆盖 Responses；已补齐 Chat Completions。

### A02 Provider Boundary 未将 RemoteDisconnected 归类为 Transport

`RemoteDisconnected` 所属 `ConnectionError` 已纳入 Provider Client transport exception，由 Runtime Retry Policy / Retry Budget 管理。

### A03 CI 依赖与完整 Gate

Mock + Runtime 验收依赖统一安装后，`tests/test_openai_mock*.py` 作为完整 Python Acceptance Gate 执行。

## 5. 二次独立 Review 阻断项与关闭情况

### R01 behavior.status 契约未完整兑现 — CLOSED

问题：
- 文档定义 `status` 为最终 HTTP Status；
- 原实现对成功路径固定返回 200，配置 201/202/206 等不会生效。

修复：
- `status` 现在作为 fail-first-N 后的最终状态使用；
- non-stream、stream、`/v1/models` 均兑现配置；
- `status` 限制为 200–599；
- `fail_status` 限制为 400–599；
- 新增 429→202、Streaming 206、Models 203、非法 1xx、非法 fail_status 的契约测试。

结果：CLOSED / PASS。

### R02 Compatibility Matrix 退化为验收摘要 — CLOSED

问题：
- 原文件失去 Endpoint/能力/状态/证据矩阵，不能承担长期兼容管理职责。

修复：
- 恢复 OpenAI Compatible Plane 矩阵；
- 恢复 Control Plane / Transport Behavior 矩阵；
- 增加 Runtime Integration 矩阵；
- 增加 Python/JS SDK 固定 Acceptance Baseline；
- 显式保留 NOT IMPLEMENTED / OUT OF V0.1。

结果：CLOSED / PASS。

### R03 Python SDK Acceptance 版本漂移 — CLOSED

原配置：`openai>=3.16,<4`。

现配置：`openai==3.16.2`。

策略：
- 稳定 Acceptance Lane 固定已验证版本；
- 如需跟踪未来 SDK，另设 latest-compatible lane，不影响稳定 Gate 可重复性。

结果：CLOSED。

## 6. 交付物

- `tools/openai_mock/server.py`
- `tools/openai_mock/__init__.py`
- `tools/openai_mock/examples/`
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
- `.github/workflows/agent-runtime-p0.yml`

## 7. 当前结论

冻结设计基线：
- AC-01～AC-15：**15 / 15 PASS**
- RT-MOCK-001～RT-MOCK-016：**16 / 16 PASS**
- 二次 Review：**3 项全部 CLOSED**
- Runtime P0 + Storage 历史回归：**119 passed**
- OpenAI Mock Python Acceptance：**43 passed**
- OpenAI JS SDK：**5/5 PASS**

当前状态：

```text
REVIEW_PASS / V0.1 RC / READY_FOR_MERGE_DECISION
```

约束：
- PR #17 当前未合并 main；
- 当前不标记 DONE；
- V0.1 不再扩 Scope；
- 下一步仅剩合并决策。
