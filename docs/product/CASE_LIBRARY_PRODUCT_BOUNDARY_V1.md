# 案例库产品边界 V1.0

## 一、产品定位

对用户只有一个“案例库”产品，不再把“重大问题案例库”作为独立案例库产品呈现。

重大问题是案例库的一个知识来源/生产通道：

```
重大问题 / ITR
      ↓
Major Knowledge（人工确认）
      ↓
CASE-PUBLISH
      ↓
Historical Case
      ↓
案例库
      ├─ Search
      ├─ Detail
      └─ Evidence
```

未来可以继续增加其他来源，例如漏测问题、一般软件问题、其他历史问题，但统一沉淀为 Historical Case，并通过同一消费契约提供服务。

## 二、职责分层

### Major Knowledge
负责重大问题事实本身：
- ISSUE_FACT
- ROOT_CAUSE
- ACTION
- VERIFICATION
- Event / ITR Scope
- Knowledge Revision
- Evidence provenance

### CASE-PUBLISH
负责把 Major Event 的人工确认知识转换并发布为 Historical Case：
- CONFIRMED-only
- EVENT isolation
- CASE_SHARED inclusion
- UNSCOPED exclusion
- stable case_id
- CREATED / REUSED / UPDATED
- atomic publish / rollback / retry

### Case Library
负责统一案例资产和消费体验：
- Historical Case
- Search
- Case Detail
- Evidence
- `historical-case/v1`

## 三、用户感知

用户不需要理解 Major Knowledge Store、CASE-PUBLISH、Historical Case artifact 的内部边界。

用户看到的是：

> 一个案例库，多个知识来源。

重大问题确认后进入同一个案例库；查询、详情、证据追溯均通过统一入口完成。

## 四、兼容性原则

本补丁只纠正产品定位、交付命名和包元数据，不改变现有运行语义：

- 不修改 `historical-case/v1`
- 不修改 Major 数据结构
- 不增加第二套 Revision
- 不修改 CASE-PUBLISH 映射规则
- 不修改 Repeat Risk 决策逻辑
- 不修改 Runtime 边界

现有 `major_case_*` 内部模块名保留，因为它们表达“来源适配器”，不代表独立案例库产品。
