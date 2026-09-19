# AI_PROJECT_ENGINEERING_CONTROL

跨子项目工程配置管理控制面（V0.3）。

## 当前治理目标

从“项目/基线登记”升级为“模块级工程治理”：

```text
Repository
   ↓
Project / Baseline
   ↓
Module Catalog
   ↓
Ownership + Dependency
   ↓
Duplication / Boundary Risk
   ↓
Project Confirmation
   ↓
Incremental Change Governance
```

## 分工原则

**控制面统一发现，各项目只确认与补差。**

项目不需要从零维护一套自己的配置管理表。控制面基于 Git/目录/import/配置/历史变更自动形成首版：
- 仓库和基线
- 主代码工作区
- 共享模块
- 依赖关系
- 重复实现候选
- 架构边界风险

项目只需确认：
1. 这个模块是不是本项目主责；
2. 有没有漏掉的项目专属模块；
3. 哪些共享关系是有意设计而非历史遗留。

## V0.3 已建立

- `PROJECT_REGISTRY.yaml`
- `BASELINE_REGISTRY.yaml`
- `REPOSITORY_INVENTORY.yaml`
- `MODULE_OWNERSHIP.yaml`
- `MODULE_CATALOG.yaml`
- `DEPENDENCY_MATRIX.yaml`
- `DEPENDENCY_GRAPH.yaml`
- `DUPLICATION_CANDIDATES.yaml`
- `DISCOVERY_CONFIRMATION.yaml`
- `ARCHITECTURE_GUARDRAILS.yaml`
- `AUDIT_REUSE_REGISTRY.yaml`
- `WORKSPACE_RULES.md`

## 当前关键结论

1. `ploblem_ai` 是“一个物理仓库 + 多个逻辑子项目 + 大量共享模块”。
2. 重大问题基线直接继承质量场景基线，并非完全独立代码库。
3. AI Client / Model Config 属于明确共享运行时。
4. 并发执行至少有三处实现，是统一 AI Orchestrator 的首批收敛对象。
5. 配置加载存在两个入口，但职责并非完全重复；先划边界，不盲目合并。
6. 打包脚本存在公共规则重复维护风险，应抽公共 packaging policy。
7. 已有审计结论默认复用，后续只做增量审计。

## 当前不做

- 不修改业务代码
- 不修改 main
- 不自动合并 Draft PR
- 不因为目录名相似就直接删除/合并代码
- 不把自动发现结果冒充项目确认事实

最后更新：2026-09-20
