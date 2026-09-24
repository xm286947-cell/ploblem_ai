# 提测范围与状态

`PRODUCT_STAGE=MVP_INTEGRATION`
`TEST_PACKAGE=READY`
`ENGINEERING_GOLDEN=PASS`
`TARGET_ENV=PENDING`
`MVP_READY=NO`

本包沿用 `main` 上的 REPEAT-WEB-001，实现 ITR 工作台 H01～H08 以及重大问题案例库 H09～H10。测试资产采用 GOLDEN-E2E-001 Draft PR #85 的工程自动测试与 Gate 文档，不把 PR #85 当作正式产品验收。目标环境需自行提供获准的真实或正式脱敏数据。

闭环：Major Event 人工确认 → Case Publish → Historical Case → Retrieval；当前 ITR → 可选漏测 Context → Repeat Risk → Candidate → Why Relevant → Cause/Measure → Evidence → Human Decision → 刷新恢复。检查 Search Error 语义、Event/ITR 隔离、Confirmed 不被 AI 覆盖、跨域 Contract、Runtime 与 Knowledge Platform 复用。

本包使用既有 `create_p0_app` 和 P0 初始化/迁移代码，不交付另一套 Web、Repeat App、Runtime 或 Knowledge Platform。截图来自页面 CI；合成工程 Golden 使用测试替身，不能代表目标环境检索质量或正式 MVP Gate。
