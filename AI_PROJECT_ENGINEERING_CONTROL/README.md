# AI_PROJECT_ENGINEERING_CONTROL

跨子项目工程配置管理控制面（V0.1 Bootstrap）。

## 目标

统一管理跨子项目的：
- 项目注册（Project Registry）
- 当前认可基线（Baseline Registry）
- 跨项目依赖（Dependency Matrix）
- 共享组件/配置引用（Shared Components）
- 状态模型与变更记录

本控制面只管理“工程态事实”，不承载业务实现代码，不替代运行时 AI Orchestrator。

## V0.1 原则

1. GitHub 是代码与工程配置唯一事实源。
2. 只登记已验证事实；未知项使用 PENDING_REGISTRATION，不猜测。
3. 基线必须至少包含 repo + branch + commit SHA。
4. current_state 与 target_state 分开，避免把规划误当现状。
5. 子项目可以共享同一物理仓库，但必须拥有独立 logical_project_id 和独立基线。
6. 主业务仓库 main 不直接修改；本目录先在独立 feature 分支上 Bootstrap，后续可迁移到独立控制仓库。

## 当前纳管范围

- MAJOR_PROBLEM_AGENT
- QUALITY_SCENARIO_AND_CASE_LIBRARY
- AI_ORCHESTRATOR
- STORAGE_LIFE_SPECIALTY

最后更新：2026-09-20
