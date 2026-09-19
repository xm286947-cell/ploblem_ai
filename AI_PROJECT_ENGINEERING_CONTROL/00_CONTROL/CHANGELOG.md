# CHANGELOG

## V0.2 — 2026-09-20

- 完成当前可写 GitHub 仓库盘点。
- 确认 `ploblem_ai` 为当前可写纳管仓库。
- 识别重大问题与质量场景的基线继承关系：`18ac9ea...` 直接基于 `c7aa301...`，仅增加 1 个提交。
- 为重大问题、质量场景登记主要代码工作区。
- 新增 MODULE_OWNERSHIP，区分 OWNED / SHARED / BOUNDARY_REVIEW_REQUIRED。
- 登记共享 AI Runtime、质量分析、数据、Web Shell、Contracts。
- 发现两个 YAML 配置加载入口，登记为边界梳理项，本轮不做代码重构。
- 将既有 Development Audit Ledger 纳入 AUDIT_REUSE_REGISTRY，默认执行增量审计，减少重复全仓审计。
- 建立跨子项目 WORKSPACE_RULES。

## V0.1 Bootstrap — 2026-09-20

- 建立跨项目 Project Registry。
- 建立 Baseline Registry。
- 建立 Dependency Matrix。
- 建立统一 Status Model。
- 登记已核验的重大问题智能体与逆向质量场景基线。
- 对 AI Orchestrator、存储写入寿命专项保留 PENDING_REGISTRATION。
- 将共享 model 配置作为受控引用项登记。
