# REPEAT-RESULT-001 Contract V1

状态：IMPLEMENTATION BASELINE

## 目标

把 REPEAT-SEARCH-001 的检索结果组织成工程师可以直接阅读、追溯并进行人工判断的 Repeat Risk Result。

```
Repeat Search Result
        ↓
REPEAT-RESULT-001
        ↓
Reviewable Repeat Result
        ├─ Historical Case
        ├─ Historical Phenomenon
        ├─ Why Relevant
        ├─ Root Causes
        ├─ Measures
        ├─ Verification
        └─ Evidence
        ↓
Human Decision
```

## 关键原则

系统负责辅助信息，不替代人工最终判断。

系统不得自动写入以下人工结论：

- REPEAT
- SIMILAR
- NOT_REPEAT
- INSUFFICIENT_EVIDENCE

Result 初始人工状态始终为：

`PENDING`

## Candidate

每个 Candidate 至少包含：

- case_id
- title
- historical_phenomenon
- retrieval_score
- rank
- why_relevant
- root_causes
- measures
- verification
- evidence_refs
- evidence
- source_ref
- detail_status

## Why Relevant

“为什么相关”是独立字段，不允许只展示相似度分数。

优先使用 Search Contract 返回的：

- retrieval_reason
- matched_fields

并转换为独立 `why_relevant[]`。

如果当前检索层没有返回足够解释依据：

- `explanation_status = INSUFFICIENT`
- 明确提示“不能仅依据相似度分数判断 Repeat”
- 不猜测、不补造原因

## Result Status

- `READY_FOR_REVIEW`：Search 成功且候选详情完整
- `NO_CANDIDATES`：检索成功但无候选
- `SEARCH_UNAVAILABLE`：检索服务不可用
- `INCOMPLETE`：搜索或候选详情不完整

`NO_CANDIDATES` 和 `SEARCH_UNAVAILABLE` 都不等于 `NOT_REPEAT`。

## Human Decision

允许人工明确保存：

- `REPEAT`
- `SIMILAR`
- `NOT_REPEAT`
- `INSUFFICIENT_EVIDENCE`

同时保存：

- decided_by
- reason
- decided_at

## Persistence

Result Snapshot 属于 Repeat Domain，自身持久化，目的是：

- 页面刷新后恢复结果
- 避免刷新自动重新执行 Search / AI
- 保留当时的查询与结果证据

Result Store 不复制 Major DB / Historical Case DB。
