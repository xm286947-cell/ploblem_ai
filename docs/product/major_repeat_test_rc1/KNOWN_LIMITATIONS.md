# 已知限制

1. `TARGET_ENV=PENDING`：尚无获准的真实或正式脱敏 ITR 与 Historical Case 完成最终目标环境验收。
2. Engineering Golden 使用仓库既有合成测试夹具与确定性 Retrieval Adapter；合成演示索引也只含测试案例，不能证明真实召回质量。
3. `historical-case/v1` 对部分老案例只提供通用 `root_cause`、`solution`；技术/管理原因及技术/管理措施未必已分别确认。
4. 缺少 Confirmed 的字段须显示“未确认 / 无已确认内容”。系统不能由 AI 自动补齐缺失的 Confirmed Case Fact。
5. H01～H10 截图来自 REPEAT-WEB-001 CI 的页面夹具，目标环境 UI、Provider、权限、数据来源和 Windows 启动仍需现场验收。
6. 正式 `MVP_READY` 需目标环境 Golden E2E 和 Release Gate 另行通过；本包不等于产品正式发布。
