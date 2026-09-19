# CHANGELOG

## V0.4 — 2026-09-20

- 冻结 AI Runtime、Execution Engine、Packaging 三个共享能力的目标边界。
- 建立 AI_RUNTIME_CONTRACT：业务层不再感知 Provider、密钥与底层 HTTP Client。
- 建立 EXECUTION_ENGINE_CONTRACT：统一串行、并行、Pipeline、重试、进度和通用状态。
- 建立 PACKAGING_POLICY：统一运行数据/敏感信息排除、Manifest与三类 Package Profile。
- 建立 MIGRATION_PLAN_V0.4，采用 Adapter First，不进行一次性大重构。
- 建立 CONSUMER_IMPACT_MATRIX，为后续共享组件修改提供跨项目回归范围。
- 将并发执行候选和 Packaging 候选升级为 DESIGN_FROZEN。
- AI_ORCHESTRATOR 明确目标包含 AI_RUNTIME + EXECUTION_ENGINE。
- 本轮未修改业务代码、模型配置值或数据库。

## V0.3 — 2026-09-20

- 对 `ploblem_ai` 执行首轮模块自动清点。
- 在质量场景基线上识别 316 个 Python 文件，其中 builder 45、quality_knowledge 84、tests 119。
- 建立 MODULE_CATALOG，按入口/业务域/AI Runtime/数据契约/解析转换/呈现/兼容发布七层分类。
- 建立 DEPENDENCY_GRAPH，并以 import、Git lineage、Web 装配关系作为证据。
- 建立 DISCOVERY_CONFIRMATION：控制面统一发现，各项目只做确认与补差。
- 建立 DUPLICATION_CANDIDATES；候选不等同重复代码结论。
- 高优先级候选：并发执行机制、打包工具链。
- 新增 ARCHITECTURE_GUARDRAILS。

## V0.2 — 2026-09-20

- 完成当前可写 GitHub 仓库盘点。
- 识别重大问题与质量场景的基线继承关系。
- 为重大问题、质量场景登记主要代码工作区。
- 新增 MODULE_OWNERSHIP、AUDIT_REUSE_REGISTRY、WORKSPACE_RULES。

## V0.1 Bootstrap — 2026-09-20

- 建立跨项目 Project Registry、Baseline Registry、Dependency Matrix 与 Status Model。
