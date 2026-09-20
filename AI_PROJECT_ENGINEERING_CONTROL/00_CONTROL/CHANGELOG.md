# CHANGELOG

## V0.7 — 2026-09-20

- 第二个 Execution Engine consumer 落地到独立质量场景实现分支。
- scenario_generation 的候选生成与历史标准化不再直接维护 ThreadPool/as_completed。
- 根据真实 consumer 需求，为共享引擎补充 completion-order 与 CONTINUE 单项异常隔离。
- 质量场景业务规则、缓存/claim、持久化及最终状态仍留在领域层。
- 建立 Draft PR #2，不修改 main，不把业务代码并入工程治理分支。
- 有效证据已回写 EXECUTION_ASSET_INDEX；完整质量场景回归尚未通过。
- PR #2 首轮专项回归触发环境熔断：执行环境缺少 pytest，三组必测套件均未执行；compileall 通过且未产生代码修改。
- 资产状态调整为 IMPLEMENTED_VALIDATION_BLOCKED；下一步先恢复测试依赖，再原样重跑，不扩展到第三个 consumer。


## V0.6 — 2026-09-20

- 新增最小共享 Execution Engine。
- builder/parallel_execution.py 改为兼容适配层，现有 ordered_map API 不变。
- ThreadPool 执行机制从旧模块下沉到共享引擎。
- V0.6 只实现 ordered sequential/parallel map；Retry、Checkpoint、Progress 暂不提前实现。
- 新增专项测试覆盖并行顺序、串行、fail-fast 异常透传与旧 API 兼容。
- 新增 V0.6 执行资产记录，并回写 EXECUTION_ASSET_INDEX。
- 未修改质量场景、重大问题、数据库和 model.yaml。

## V0.5 — 2026-09-20

- 建立轻量“项目执行资产与复用机制”。
- 每个阶段只沉淀：目标、计划、有效证据、可复用结论。
- 已有 Development Audit Ledger 继续作为原始证据源，不复制内容。
- 默认先复用 ACTIVE 资产，只有触发失效条件才重新验证。
- 临时日志、重复截图、无结论中间过程默认不归档。

## V0.4 — 2026-09-20

- 冻结 AI Runtime、Execution Engine、Packaging 三个共享能力的目标边界。
- 建立 AI_RUNTIME_CONTRACT、EXECUTION_ENGINE_CONTRACT、PACKAGING_POLICY。
- 采用 Adapter First，不进行一次性大重构。

## V0.3 — 2026-09-20

- 建立 MODULE_CATALOG、DEPENDENCY_GRAPH、DISCOVERY_CONFIRMATION 与 ARCHITECTURE_GUARDRAILS。

## V0.2 — 2026-09-20

- 完成仓库、模块归属和审计复用初始治理。

## V0.1 Bootstrap — 2026-09-20

- 建立 Project Registry、Baseline Registry、Dependency Matrix 与 Status Model。