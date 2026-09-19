# AI_PROJECT_ENGINEERING_CONTROL

跨子项目工程配置管理控制面（V0.5）。

## 核心目标

减少四类重复工作：

- 重复理解
- 重复设计
- 重复开发
- 重复审计

治理方式不是保存所有过程，而是统一沉淀：

```text
目标
  ↓
计划
  ↓
执行
  ↓
有效证据
  ↓
可复用结论
  ↓
其他项目直接复用
```

## V0.5 轻量执行资产机制

只新增三个核心入口：

- `30_EXECUTION_ASSETS/EXECUTION_ASSET_INDEX.yaml`：查目前有什么可以直接复用。
- `30_EXECUTION_ASSETS/PROJECT_EXECUTION_RECORD_TEMPLATE.yaml`：每个项目阶段按同一最小格式记录。
- `00_CONTROL/EXECUTION_ASSET_RULES.md`：定义什么值得归档、什么时候需要重验。

已有 `docs/DEVELOPMENT_AUDIT_LEDGER.md` 不复制，作为现有证据源被索引引用。

## 使用原则

开始任务先查资产索引。已有 ACTIVE 结论且未触发失效条件，直接复用，不重新全量审计。

执行中只记录对后续有价值的数据：Test / E2E / Audit / Scan / Metric / Manifest / Architecture Decision。

结束任务只把可复用信息登记进索引，不保存大量过程日志、重复截图和无结论探索过程。

## 共享组件方向

V0.4 已冻结：
1. Execution Engine
2. AI Runtime
3. Packaging

V0.5 的执行资产机制作为后续组件开发的统一前置动作。

最后更新：2026-09-20
