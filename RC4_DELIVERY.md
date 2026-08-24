# RC4 DELIVERY — Analysis Workbench UI

Version: KNOWLEDGE_QUALITY_ISSUE_ANALYSIS_ENGINE_V1.0_M5_RC4
Baseline: M5 RC3
Scope: Presentation / Web UX only

## Goal

把 Issue Detail 从 JSON/数据库字段查看页调整为质量问题分析工作台。默认优先展示业务结论，Raw / Normalized / Debug 降级为追溯信息。

## Delivered

- Issues List 增加 AI Status / Recurrence Risk / T-M-G Gap Summary。
- Issue Detail 重构为：问题事实 → 原因分析 → 再发防控 → 原始与追溯。
- Occurrence 与 Escape 双栏独立展示。
- Recurrence Risk 直接展示 HIGH/MEDIUM/LOW、共性问题、横向治理、潜在影响产品。
- Technical / Management / Governance Gap 三列卡片化展示。
- Original / Normalized / Version History / Analysis Run / AI Debug 改为默认折叠的 Traceability 区域。
- AI Run / Retry 修正为 Web 页面 POST `/analysis/{knowledge_id}` 后重定向回详情页。
- 不修改 AI 算法、不修改核心数据库 Schema、不修改 Repeat Case Similarity / Decision。

## Modified Files

- quality_knowledge/web/app.py
- quality_knowledge/web/templates/issues.html
- quality_knowledge/web/templates/issue_detail.html
- quality_knowledge/web/static/app.css
- tests/test_rc4_analysis_workbench_ui.py
- RC4_DELIVERY.md

## Test Result

- RC4 UI专项：2 passed
- Full Regression：159 passed / 0 failed

## Acceptance Focus

业务用户默认看到：

1. 问题事实
2. 为什么发生
3. 为什么流出
4. 再发风险
5. Technical / Management / Governance Capability Gap

工程/质量人员按需展开：

- Original Raw
- Normalized JSON
- Issue Version History
- Analysis Run History
- AI Debug
