# AI_PROJECT_ENGINEERING_CONTROL

跨子项目工程配置管理控制面（V0.8）。

## 核心目标

减少四类重复工作：

- 重复理解
- 重复设计
- 重复开发
- 重复审计

当前统一执行链路：

```text
目标 / 基线
    ↓
复用已有资产
    ↓
Preflight
Runtime / Dependencies / Test Framework
    ↓
正式执行
    ↓
有效证据
    ↓
可复用结论
```

## 轻量规则

只保留真正影响后续工作的内容：
- 目标与验收；
- Preflight 结果；
- 计划；
- Test / E2E / Audit / Scan / Metric / Manifest；
- 可复用结论及失效条件。

已有 `docs/DEVELOPMENT_AUDIT_LEDGER.md` 继续作为证据源引用，不复制。

Preflight 未通过时：
- 不修改代码；
- 不把环境失败判成代码失败；
- 记录熔断原因；
- 环境恢复后原样重跑。

## 共享组件方向

当前共享收敛对象：
1. Execution Engine
2. AI Runtime
3. Packaging

质量场景 Execution Engine consumer 已实现，但专项回归当前因执行环境缺 pytest 处于 `IMPLEMENTED_VALIDATION_BLOCKED`。

最后更新：2026-09-20
