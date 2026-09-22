# AI_PROJECT_ENGINEERING_CONTROL

跨子项目工程配置管理控制面（V0.9）。

## 核心目标

减少：
- 重复理解
- 重复设计
- 重复开发
- 重复审计
- **需求、方案、代码和验收之间的断链**

## 统一执行链路

```text
需求 / 目标
    ↓
方案 / 决策
    ↓
CMO Trace
    ↓
Preflight
    ↓
编码 / PR / Commit
    ↓
测试 / 验收
    ↓
Baseline / Release
    ↓
有效证据归档
```

## CMO 追溯

CMO 不重复 PMO，也不保存正文。

统一入口：

`40_CMO_TRACEABILITY/TRACE_REGISTER.yaml`

每条链只维护：

`REQ → SOL → CHG → Commit/PR → VER → Baseline`

CMO回答的是：

> 这个需求用了哪套方案、改了哪些代码、通过了什么验证、最后进入哪个基线？

当前 TRACE-EXEC-001 已覆盖 Execution Engine → 质量场景 PR #2；由于 pytest 环境阻断，目前状态为 `VALIDATION_BLOCKED`，不会错误标记为已基线。

## 执行资产与 Preflight

任务开始先复用已有资产，再进行 Runtime / Dependencies / Test Framework Preflight。

Preflight 未通过：
- 不修改代码；
- 不把环境失败判成代码失败；
- 恢复环境后原样重跑。

最后更新：2026-09-20
