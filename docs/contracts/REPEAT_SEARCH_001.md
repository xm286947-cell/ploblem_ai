# REPEAT-SEARCH-001 Contract V1

状态：IMPLEMENTATION BASELINE

## 目标

Repeat Risk 只能通过正式 Historical Case Consumer Contract 查询历史案例：

```
Repeat Query Snapshot
        ↓
REPEAT-SEARCH-001
        ↓
historical-case/v1
        ├─ search_repeat_cases()
        └─ get_case()
        ↓
Repeat Search Result
```

## 禁止

Repeat Domain 不允许：

- 直接读 Major DB
- 直接读 Knowledge DB
- 直接读内部 JSON
- 直接读 Retrieval Index
- 通过跨域 SQL 查询 Historical Case
- 读取内部 artifact path

Historical Case 内部如何完成检索不属于 Repeat Domain。

## Query

查询主体来自 REPEAT-ITR-001 的不可变 Query Snapshot。

当前 ITR 始终是主 Subject。

关联漏测问题仅在 `include_missed_test=true` 时作为补充 cause/context 进入查询，不成为第二个平级 Subject。

## Search Result

每个 Candidate 至少包含：

- case_id
- retrieval_score
- rank
- historical_phenomenon
- root_causes
- measures
- verification
- evidence_refs
- source_ref
- retrieval_reason

`retrieval_reason` 直接来自 Historical Case Search Contract；REPEAT-RESULT-001 后续负责把它组织成面向工程师的“为什么相关”。

## 状态

- `SUCCESS`：Search + Candidate Detail 全部完成
- `NO_CANDIDATES`：查询成功但没有候选
- `SEARCH_UNAVAILABLE`：Historical Case Search 服务不可用
- `INCOMPLETE`：搜索或候选详情契约不完整

禁止：

```
Search Error → NOT_REPEAT
```

正确行为：

```
CASE_SERVICE_UNAVAILABLE → SEARCH_UNAVAILABLE
CASE_CONTRACT_INVALID    → INCOMPLETE
Candidate Detail Error   → INCOMPLETE
```

人工 Repeat Decision 不在本任务中产生。
