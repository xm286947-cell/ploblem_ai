# MVP 提测操作指南

先按 `DEPLOYMENT_TEST_GUIDE.md` 启动。合成演示仅用于熟悉流程；正式 Gate 请在获准的目标环境使用真实或正式脱敏样本，并填写 `TARGET_UAT_CHECKLIST.md`。每步记录 ITR、Case、Query ID、Evidence ID、时间和执行人。

| 场景 | 操作与期望 |
| --- | --- |
| A 有漏测且带入 | 打开 `K-ITR-1` 工作台，勾选关联漏测，点“查询历史类似问题”；应得到 Candidate，Query Trace 中 `include_missed_test=true`、`missed_test_ref=MISS-1`。 |
| B 有漏测但不带入 | 在同一工作台取消勾选后再查询；仍可得到 Candidate，Query Trace 不含漏测内容。 |
| C 无漏测 | 打开 `K-ITR-2` 后查询；工作台不显示无效勾选，ITR 仍是唯一 Subject。 |
| D Evidence | 在 Candidate 先看“为什么相关”，再看历史现象、原因、措施，打开 Evidence Drawer，核对来源、定位和原文后关闭，回到原 Candidate。 |
| E 人工判断 | 分别以新 Query 验证 `REPEAT`、`SIMILAR`、`NOT_REPEAT`、`INSUFFICIENT_EVIDENCE`；保存后核对确认人、时间、说明。四项需分开执行，不能让系统自动决定。 |
| F 刷新 | 保存 Decision 后刷新；同一 Query、结果和 Decision 恢复，Search/AI 调用次数不增加。 |
| G 案例库 | 打开“重大问题案例库”，默认列表只显示 `PUBLISHED`；打开详情，再下钻 Evidence 和原始来源。 |
| H EMPTY | 在目标环境使用已批准的空结果注入；显示“本次查询未检索到符合当前条件的历史案例”，不能显示“当前问题不是重复问题”。 |
| I SEARCH_UNAVAILABLE | 在目标环境注入检索不可用；保留 ITR 与 Context，可重试，不产生 Repeat 判断。 |
| J INCOMPLETE | 在目标环境注入部分详情或 Evidence 缺失；已有 Candidate 可见，同时明确指出缺失内容。 |

H01～H10 浏览器截图位于 `evidence/repeat_web_h01_h10/`，来自 REPEAT-WEB-001 CI Artifact；它们证明页面实现状态，不替代本包目标环境 UAT。合成演示的预期 Case 为 `HCASE-1`，其中 ITR、漏测和 Historical Case 文本来自仓库既有测试夹具。
