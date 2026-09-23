# REPEAT-ITR-001 Contract V1

状态：IMPLEMENTATION BASELINE

## 目标

把 ITR 彻底解决工作台中的当前 ITR 转换为 Repeat Risk 的正式查询 Subject，并保存不可变 Query Snapshot。

## 冻结规则

- 当前 ITR 始终是 REQUIRED 主 Subject。
- Repeat Risk 不复制/维护第二套 ITR 主数据。
- Repeat Risk 只保存 ITR 引用和本次查询快照。
- 关联漏测问题仅是 OPTIONAL Context。
- 未勾选时不得把漏测内容混入 Query Snapshot。
- 没有关联漏测问题时 Repeat Risk 仍可正常执行。
- Query Trace 必须能够回答“当时用什么信息查的”。

## RepeatQuerySubject

```
RepeatQuerySubject
├─ itr_ref              REQUIRED
├─ itr_snapshot         REQUIRED
│  ├─ itr_id
│  ├─ problem_description
│  ├─ product
│  ├─ version
│  ├─ scene
│  ├─ existing_context
│  ├─ itr_version
│  └─ source = ITR_RESOLUTION_WORKBENCH
└─ optional_context
   └─ missed_test_ref   OPTIONAL
```

## Query Trace

保存：

- query_id
- subject_ref
- itr_version
- itr_snapshot
- include_missed_test
- missed_test_ref
- optional_context
- query_time
- algorithm_version
- correlation_id

## Domain Boundary

ITR 主数据由宿主 ITR Workbench 提供；Repeat Domain 通过 `ITRSubjectSource` 接口读取，不直接拥有 ITR DB。

Query Trace 使用 Repeat Domain 自己的 SQLite Store，不写 Major DB，不写 Historical Case DB。

## Error

- ITR_REF_REQUIRED
- ITR_NOT_FOUND
- ITR_PROBLEM_DESCRIPTION_REQUIRED
- MISSED_TEST_NOT_AVAILABLE
- MISSED_TEST_NOT_RELATED
- MISSED_TEST_NOT_FOUND
