# GOLDEN-E2E-001｜重大问题案例库 × Repeat Risk 最终 MVP Gate

基线：`main@d16ed17f2ca6fed39d1c4a17192d200601d7330d`（REPEAT-WEB-001）。

## 样本与结论口径

本仓库 `main` 未提供带使用授权和脱敏说明的真实内部 ITR Golden 文件。本次自动测试沿用仓库已存在的**合成**测试内容：`tests/test_repeat_web_mvp.py` 的当前 ITR、漏测和历史案例文本，以及 `tests/test_case_publish_service.py` 的 Major Event、发布和来源证据结构。新增测试没有读取真实业务库、生产/UAT 系统或外部内部样本。确定性的 Retrieval Adapter 从本次发布的检索文档中取候选，经 `historical-case/v1` Contract 提供给 Repeat；它是工程测试替身，不能证明目标环境检索质量。

| Gate | 状态 | 证据或缺口 |
| --- | --- | --- |
| 合成工程 Golden E2E | 本地 `PASS`；CI 待 PR 执行 | `tests/test_golden_e2e_001.py`、JUnit XML |
| Case Publish / Repeat / Web Regression | 本地 `PASS`；CI 待 PR 执行 | 72 passed、failure delta = 0；`.github/workflows/golden-e2e-001.yml` |
| 真实或已授权脱敏 ITR + Historical Case 目标环境 E2E | `TARGET_ENV_PENDING` | 仓库无可核实的授权样本、目标环境执行记录 |
| 最终 MVP Gate | `BLOCKED` | 真实 Golden Path 与 Release Gate 尚未验收 |
| `MVP_READY` | `NO` | 待目标环境 Golden、回归及正式 Release Gate 均通过 |

## 工程测试证明范围

一条合成链路从 Major Event 的人工 `CONFIRMED` 事实与 Evidence 开始，发布 Historical Case；检查重复发布稳定 Case ID、已发布列表、详情中的根因/措施/验证/Evidence，并确认同一案例的其他 Event 和 `PENDING` AI 根因未进入发布内容。当前 ITR 从既有工作台发起 Repeat Query，明确勾选漏测 Context；检查 Query Trace、Candidate 的 Why Relevant、原因、措施和 Evidence 下钻，然后保存人工 `SIMILAR` 判断。读取恢复接口模拟刷新，断言 Query ID、Decision 保留且检索调用次数不增加。第二次查询模拟 Search 故障并取消漏测 Context，断言 `SEARCH_UNAVAILABLE`、人工判断仍为 `PENDING`，没有 `NOT_REPEAT`。另一个 ITR 不能恢复首个 ITR 的结果。静态边界检查覆盖 Repeat 域对 Major/Knowledge Repository 的依赖；本变更没有新增 Runtime、Knowledge Platform、Contract 或 Schema。

## 目标环境待办

在获准处理的目标环境中，由数据 Owner 选定真实或正式脱敏的历史 Major Event/ITR、已发布 Case、当前 ITR（含可选漏测），记录样本标识、脱敏/授权依据、环境、版本、执行人和时间。按同一链路执行 UI 与服务验证，并保存发布记录、检索结果、Evidence 来源、人工确认与 Decision、刷新恢复、错误注入、Event/ITR 隔离、CI/回归结果。只有这些证据审核通过，才能把 `TARGET_ENV_PENDING` 关闭并评估 `MVP_READY`。正式产品编包属于后续 Release Gate。
