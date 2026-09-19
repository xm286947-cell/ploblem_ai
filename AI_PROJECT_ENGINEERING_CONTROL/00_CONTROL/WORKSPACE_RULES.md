# 跨子项目工作区规则 V0.2

## 1. 开始任务前

必须明确四件事：

1. PROJECT_ID
2. BASELINE_BRANCH + BASELINE_COMMIT
3. PRIMARY_WORKSPACE
4. SHARED_MODULES_TOUCHED

缺少任一项时，不进入跨模块修改。

## 2. 修改范围

- OWNED 模块：项目可直接修改，但不得越界修改其他项目 OWNED 模块。
- SHARED 模块：修改前必须查询 MODULE_OWNERSHIP 与 DEPENDENCY_MATRIX。
- BOUNDARY_REVIEW_REQUIRED：原则上只做最小修复，不新增新的重复实现；需要新增时先登记设计决策。

## 3. 共享模块变更

任何共享模块变更必须记录：
- 修改原因
- changed files
- 受影响 consumer
- 是否改变配置/接口/Schema
- 对应专项测试
- 是否需要跨项目回归

## 4. 审计

默认使用增量审计：
- 读取 AUDIT_REUSE_REGISTRY
- 读取已有 Development Audit Ledger
- 只审计 changed files + 直接上下游
- 无触发条件禁止重复全工程审计

## 5. 基线继承

当前重大问题基线直接继承质量场景基线。重大问题项目开发时：
- 不得把继承来的质量场景模块自动视为本项目 OWNED
- 修改 scenario/reverse_quality 等模块时按跨项目共享/越界变更处理
- 后续优先将隐式“整仓继承”收敛成显式组件依赖

## 6. 新增子项目

先创建项目注册项和模板文件，再创建业务代码：
- PROJECT_REGISTRY
- BASELINE_REGISTRY
- project profile
- primary workspace
- shared dependencies
- audit/release rules

目标是避免“代码已有、归属未知”。
