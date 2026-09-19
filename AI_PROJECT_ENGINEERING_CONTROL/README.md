# AI_PROJECT_ENGINEERING_CONTROL

跨子项目工程配置管理控制面（V0.4）。

## 当前定位

控制面负责回答五个问题：

1. 每个项目基于哪个 Repo / Branch / Commit？
2. 每块代码是谁主责、谁在复用？
3. 改一个共享模块会影响谁？
4. 哪些公共能力正在重复实现？
5. 公共能力应该收敛到哪个稳定接口？

## 治理链路

```text
Repository / Git Baseline
          ↓
Project Registry
          ↓
Module Catalog + Ownership
          ↓
Dependency Graph
          ↓
Duplication / Boundary Risk
          ↓
Shared Component Contract
          ↓
Adapter Migration
          ↓
Incremental Audit + Impact Regression
```

## V0.4 三个共享收敛对象

### 1. AI Runtime

统一：
- model / provider / agent
- runtime config
- provider client
- timeout / retry
- Mock / real 标识
- response contract

业务项目保留 Prompt、输入事实和业务校验。

### 2. Execution Engine

统一：
- SEQUENTIAL / PARALLEL / PIPELINE
- concurrency
- retry / timeout
- progress
- checkpoint
- generic task/item status

业务项目保留业务阶段和领域成功标准。

### 3. Packaging

统一：
- include/exclude policy
- runtime data / secret exclusion
- SHA256
- Manifest
- baseline metadata
- FULL / DELTA / PROJECT_DELTA

项目只维护 package profile。

## 当前实施策略

**Adapter First，不做 Big Bang Rewrite。**

优先顺序：

```text
Execution Engine
      ↓
AI Runtime
      ↓
Packaging Policy
      ↓
Deprecation / Cleanup
```

V0.4 仍然只做治理和接口冻结，不修改业务代码、不修改 main。

最后更新：2026-09-20
