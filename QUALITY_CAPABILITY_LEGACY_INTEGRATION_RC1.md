# Quality Capability Legacy Integration RC1

## 基线原则

- 继续使用稳定老版 `knowledge-web`、原页面、原路由和原数据库。
- 不修改 `quality_issue`、Mapping、Import、AI、Human Analysis 等原表结构。
- 新能力只写入 `qc_*` 扩展表，通过 `knowledge_id`、`issue_version_id` 关联。

## 本次实现

- 老问题详情增加标准化发生/流出 MRC、领域/生命周期标签、证据与来源。
- 老质量洞察增加质量工程、质量管理、横向治理口径。
- 老质量洞察增加 MRC×质量能力、生命周期×质量能力矩阵。
- 已有分析进入洞察时自动幂等投影；新单条/批量分析完成后立即更新投影。
- 老批量 AI 页面支持 1～4 并发，默认 2；任务后台运行并显示进度、成功数、失败数和最终状态。
- 原工作台、问题详情、人工分析、原始/标准化数据、技术追溯、Mapping、数据接入和导出流程保持原样。

## 数据库变化

仅新增：`qc_analysis_projection`、`qc_issue_tag`、`qc_issue_mrc`、`qc_analysis_evidence`。

## 验证

`python -m pytest -q tests`：374 passed，0 failed。
