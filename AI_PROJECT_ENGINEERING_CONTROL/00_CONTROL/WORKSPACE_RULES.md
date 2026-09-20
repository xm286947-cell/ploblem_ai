# 跨子项目工作区规则 V0.9

## 1. 开始任务前

必须先明确：

1. PROJECT_ID
2. BASELINE_BRANCH + BASELINE_COMMIT
3. PRIMARY_WORKSPACE
4. SHARED_MODULES_TOUCHED

存在代码/契约变更时，再明确：

5. TRACE_ID
6. REQUIREMENT_REF
7. SOLUTION / DECISION_REF

纯只读调研任务可不建 TRACE_ID。

然后执行 Preflight：
- Runtime 可用；
- 依赖可安装/可解析；
- 测试框架可运行。

Preflight 未 PASS 时，禁止进入代码修改，按环境熔断处理。

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

## 4. CMO追溯

代码修改后必须回填：
- CHANGE_ID
- Branch / Commit / PR
- Changed Files

验证后必须回填：
- VERIFICATION_ID
- 验证状态和证据

Verification 未通过时不得标记 BASELINED。

统一追溯入口：
`40_CMO_TRACEABILITY/TRACE_REGISTER.yaml`

## 5. 审计

默认使用增量审计：
- 先读取 EXECUTION_ASSET_INDEX / AUDIT_REUSE_REGISTRY；
- 复用仍有效的既有结论；
- 只审计 changed files + 直接上下游；
- 无触发条件禁止重复全工程审计。

## 6. 基线继承

当前重大问题基线直接继承质量场景基线。重大问题项目开发时：
- 不得把继承来的质量场景模块自动视为本项目 OWNED；
- 修改 scenario/reverse_quality 等模块时按跨项目共享/越界变更处理；
- 后续优先将隐式“整仓继承”收敛成显式组件依赖。

## 7. 新增子项目

先创建项目注册项和模板文件，再创建业务代码：
- PROJECT_REGISTRY
- BASELINE_REGISTRY
- project profile
- primary workspace
- shared dependencies
- audit/release rules
- CMO Trace（如有开发变更）

目标是避免“代码已有、归属未知”和“代码已改、需求来源未知”。
