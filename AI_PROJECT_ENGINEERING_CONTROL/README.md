# AI_PROJECT_ENGINEERING_CONTROL

跨子项目工程配置管理控制面（V0.2）。

## 目标

统一管理跨子项目的：
- 项目注册（Project Registry）
- 当前认可基线（Baseline Registry）
- 跨项目依赖与基线继承（Dependency / Lineage）
- 代码工作区与模块归属（Module Ownership）
- 共享组件/配置引用（Shared Components）
- 审计结论复用（Audit Reuse）
- 状态模型与变更记录

本控制面只管理“工程态事实”，不承载业务实现代码，不替代运行时 AI Orchestrator。

## V0.2 原则

1. GitHub 是代码与工程配置事实源。
2. 只登记已验证事实；未知项使用 PENDING_REGISTRATION，不猜测。
3. 基线必须至少包含 repo + branch + commit SHA。
4. current_state 与 target_state 分开，避免把规划误当现状。
5. 子项目可以共享同一物理仓库，但必须拥有独立 logical_project_id、基线和 workspace。
6. 共享模块必须登记 owner / consumers；修改共享模块时先做影响分析。
7. 已有审计结论默认复用，只有代码实质变化、E2E反证、需求变化或契约升级时重新审计。
8. main 不作为当前开发基线直接修改；控制面继续在独立 feature 分支维护。
9. 后续新增子项目必须先注册，再开发；禁止“先写代码、后补归属”。

## 当前已确认工程形态

`ploblem_ai` 当前属于“一个物理仓库 + 多个逻辑子项目 + 共享基础模块”的组合仓库。

已确认的两条逻辑子项目基线：
- QUALITY_SCENARIO_AND_CASE_LIBRARY → `feature/reverse-quality-v01@c7aa301...`
- MAJOR_PROBLEM_AGENT → `handoff/req022-rc1-failed-acceptance@18ac9ea...`

重大问题基线是在质量场景基线 `c7aa301...` 上追加 1 个提交形成，因此当前存在明确的基线继承关系，不应把两者当成完全独立代码库。

## 当前纳管范围

- MAJOR_PROBLEM_AGENT
- QUALITY_SCENARIO_AND_CASE_LIBRARY
- AI_ORCHESTRATOR
- STORAGE_LIFE_SPECIALTY

## V0.2 新增控制项

- `REPOSITORY_INVENTORY.yaml`：仓库接入与可写状态
- `MODULE_OWNERSHIP.yaml`：主要代码/模块工作区、owner、consumer、交叉边界
- `AUDIT_REUSE_REGISTRY.yaml`：复用既有审计结论，减少重复全仓审计
- `WORKSPACE_RULES.md`：跨子项目改代码的固定规则

最后更新：2026-09-20
