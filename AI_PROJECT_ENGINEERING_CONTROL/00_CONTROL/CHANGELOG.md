# CHANGELOG

## V0.8 — 2026-09-20

- 将 Preflight 正式纳入所有代码任务前置门禁。
- Preflight 最小检查 Runtime、Dependencies、Test Framework。
- Preflight 未 PASS 时禁止进入代码修改，统一按环境熔断处理。
- PROJECT_EXECUTION_RECORD_TEMPLATE 增加 Preflight 结果与 circuit breaker 字段。
- 将 PR #2 pytest 缺失熔断登记为首个可复用环境证据。
- 保持轻量：不新增独立平台或日志系统。

## V0.7 — 2026-09-20

- 第二个 Execution Engine consumer 落地到独立质量场景实现分支。
- scenario_generation 的候选生成与历史标准化不再直接维护 ThreadPool/as_completed。
- 根据真实 consumer 需求，为共享引擎补充 completion-order 与 CONTINUE 单项异常隔离。
- 质量场景业务规则、缓存/claim、持久化及最终状态仍留在领域层。
- 建立 Draft PR #2，不修改 main，不把业务代码并入工程治理分支。
- PR #2 首轮专项回归触发环境熔断：执行环境缺少 pytest，三组必测套件均未执行；compileall 通过且未产生代码修改。
- 资产状态调整为 IMPLEMENTED_VALIDATION_BLOCKED；下一步先恢复测试依赖，再原样重跑，不扩展到第三个 consumer。

## V0.6 — 2026-09-20

- 新增最小共享 Execution Engine。
- builder/parallel_execution.py 改为兼容适配层，现有 ordered_map API 不变。
- ThreadPool 执行机制从旧模块下沉到共享引擎。

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
