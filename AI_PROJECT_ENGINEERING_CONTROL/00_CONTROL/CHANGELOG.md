# CHANGELOG

## V0.9 — 2026-09-20

- 新增 CMO 轻量追溯链，统一管理 Requirement → Solution → Change → Code → Verification → Baseline。
- 新增 TRACE_REGISTER 与 TRACE_ITEM_TEMPLATE，不复制需求/方案/测试正文，只保存 ID、状态和引用。
- 代码/契约变更任务新增 TRACE_ID、REQUIREMENT_REF、SOLUTION_REF 最小要求。
- Verification 未通过不得进入 BASELINED 状态。
- 以 Execution Engine / 质量场景 PR #2 建立首条真实 TRACE-EXEC-001。
- TRACE-EXEC-001 当前明确为 VALIDATION_BLOCKED，保留 pytest 环境阻断事实。
- CMO 与 PMO 分工明确：PMO管责任/计划/进度，CMO管配置项/版本/追溯链。

## V0.8 — 2026-09-20

- 将 Preflight 正式纳入所有代码任务前置门禁。
- Preflight 最小检查 Runtime、Dependencies、Test Framework。
- Preflight 未 PASS 时禁止进入代码修改，统一按环境熔断处理。

## V0.7 — 2026-09-20

- 第二个 Execution Engine consumer 落地到独立质量场景实现分支。
- PR #2 首轮专项回归触发环境熔断：执行环境缺少 pytest，未产生代码修改。

## V0.6 — 2026-09-20

- 新增最小共享 Execution Engine。
- builder/parallel_execution.py 改为兼容适配层。

## V0.5 — 2026-09-20

- 建立轻量项目执行资产与复用机制。

## V0.4 — 2026-09-20

- 冻结 AI Runtime、Execution Engine、Packaging 三个共享能力目标边界。

## V0.3 — 2026-09-20

- 建立 MODULE_CATALOG、DEPENDENCY_GRAPH、DISCOVERY_CONFIRMATION 与 ARCHITECTURE_GUARDRAILS。

## V0.2 — 2026-09-20

- 完成仓库、模块归属和审计复用初始治理。

## V0.1 Bootstrap — 2026-09-20

- 建立 Project Registry、Baseline Registry、Dependency Matrix 与 Status Model。
