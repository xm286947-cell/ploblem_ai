# CMO 追溯链规则 V0.9

CMO 的职责是**保证可追溯，不代替需求、架构、开发、测试或 PMO。**

## 1. 管什么

CMO 统一维护：

```text
Requirement
    ↓
Solution / Decision
    ↓
Change
    ↓
Code
    ↓
Verification
    ↓
Baseline / Release
```

每一层只保存：
- 唯一 ID；
- 当前状态；
- 源文件/Issue/PR/Commit/测试证据引用；
- 上下游关系；
- 未闭环项。

**不复制需求正文、方案正文、测试日志和代码内容。**

## 2. 不管什么

CMO 不负责：
- 判断需求优先级；
- 设计方案；
- 项目排期；
- 写代码；
- 代替测试验收；
- 重复保存 PMO 状态。

PMO 回答“什么时候、谁负责、进度怎样”；  
CMO 回答“这个需求最终变成了哪套方案、哪些代码、经过什么验证、进入了哪个基线”。

## 3. 最小门禁

### 开发前

有代码/契约变更的任务至少必须有：

- TRACE_ID
- REQUIREMENT_REF
- SOLUTION/DECISION_REF
- BASELINE_BRANCH + BASELINE_COMMIT

纯只读调研可以暂不建 TRACE_ID。

### 编码后

必须补：

- CHANGE_ID
- Branch
- Commit
- PR
- Changed Files

### 验证后

必须补：

- VERIFICATION_ID
- 测试/验收证据
- PASS / FAIL / BLOCKED

### 进入基线前

只有 Verification = VERIFIED 才允许 Trace = BASELINED。

环境阻断不是代码失败；保持 VALIDATION_BLOCKED。

## 4. 追溯完整性

一条开发追溯链的最小完整形态：

```text
REQ
 ↓
SOL
 ↓
CHG
 ↓
Commit / PR
 ↓
VER
 ↓
Baseline
```

允许阶段性为空，但必须明确状态，例如：
- IMPLEMENTED + VALIDATION_BLOCKED
- VERIFIED + NOT_BASELINED

禁止用“已完成”掩盖缺失验证或未进入基线。

## 5. 变更与回溯

后续发生变更时，不重建整条链：
- 新增一个 CHANGE；
- 指向同一 Requirement/Solution，或新的 Solution Decision；
- 追加新的 Verification；
- 老的证据保留为历史事实。

只有需求或方案被正式替代时，原 Trace 才标记 SUPERSEDED。

## 6. CMO 日常动作

CMO 只做四个动作：

1. 新任务登记 Trace ID；
2. PR/Commit 后补代码链接；
3. 验收后补 Verification；
4. 发布/基线后补 Baseline。

这样可以保持轻量，同时做到从需求一路追到代码和发布。
