# QUALITY CAPABILITY P2 RC1 DELIVERY

## IMPLEMENTATION_STATUS

`IMPLEMENTED / REAL_AI_ACCEPTANCE_PENDING`

稳定老版问题工作台、详情、人工分析、Mapping、数据接入、批量分析和质量洞察继续作为主流程。本阶段未重写老前端，也未迁移或替换老数据库。

已实现：AI 原生质量分析契约、人工有效结论、质量洞察闭环、软件问题中的硬件关联与器件失效、批量任务持久化及失败重试。

## MIGRATION_RESULT

- 不要求迁移旧数据库。
- 新能力使用自动创建的 `qc_*` 扩展表。
- 原有 Knowledge、Mapping、配置、AI 结果和人工分析表保持不变。
- 补丁覆盖后首次启动会幂等创建扩展表，不覆盖既有数据。

## E2E_RESULT

- 使用 `outputs/quality_capability_p1/plc_quality_issue_full_fields_e2e.xlsx` 在全新临时 SQLite 数据库验收。
- 59 个源字段：49 Structured、10 Extension、0 Raw Only、0 Unmatched，Coverage 100%。
- 5 条 PLC 问题：5 New、0 Failed，Import `COMPLETED`。
- 数据库：5 Issues、5 Current Versions、5 Raw Records。
- Mapping 重复歧义告警已修复：PLC 从重复 2 条收敛为 1 条真实警告，invalid=0。
- 真实四阶段 AI E2E 尚待运行环境设置 `acca` 并连通 `http://127.0.0.1:8000/v1` 的 `dtcoder`。

## MODIFIED_FILES

- `quality_knowledge/models/analysis.py`
- `quality_knowledge/response_normalizer.py`
- `quality_knowledge/prompts/occurrence.md`
- `quality_knowledge/prompts/escape.md`
- `quality_knowledge/prompts/capability_gap.md`
- `quality_knowledge/capability_extension.py`
- `quality_knowledge/batch_analysis_jobs.py`
- `quality_knowledge/mapping/migration.py`
- `quality_knowledge/repositories/v1_repository.py`
- `quality_knowledge/services/knowledge_issue_service.py`
- `quality_knowledge/services/v1_analysis_service.py`
- `quality_knowledge/web/app.py`
- `quality_knowledge/web/templates/analysis.html`
- `quality_knowledge/web/templates/issue_detail.html`
- `quality_knowledge/web/templates/statistics.html`
- `tests/test_prompt_v2_human_confirmation.py`
- `tests/test_quality_capability_legacy_integration.py`
- `QUALITY_CAPABILITY_NEXT_PHASE_TASK_BASELINE_V1.0.md`
- `P2_ACCEPTANCE_STATUS.md`
- `QUALITY_CAPABILITY_P2_RC1_DELIVERY.md`

## DATABASE/CONTRACT_CHANGES

新增扩展表：

- `qc_analysis_projection`
- `qc_issue_tag`
- `qc_issue_mrc`
- `qc_analysis_evidence`
- `qc_human_revision`
- `qc_issue_hardware_component`
- `qc_hardware_failure_analysis`
- `qc_batch_analysis_job`

Occurrence/Escape AI 输出新增原生 MRC、生命周期和来源信息；Occurrence 支持硬件相关性与多个器件失效对象；Capability Gap 增加 `source_type`。旧结果仍可由归一化层读取。

## TEST_RESULT

```text
380 passed
0 failed
```

## COMPATIBILITY_RESULT

- 老版页面和路由继续使用。
- 老业务表不改 Schema。
- 老 AI 结果保持可读，并可投影为新洞察。
- 人工结论优先但保留 AI 原值和审计记录。
- 补丁基线：`QUALITY_CAPABILITY_LEGACY_INTEGRATION_RC1_PATCH` 已应用的工程。

## RELEASE_PACKAGE

- 增量补丁：`QUALITY_CAPABILITY_P2_RC1_PATCH.zip`
- 完整工程：`KNOWLEDGE_QUALITY_ISSUE_ANALYSIS_ENGINE_V1.1_QUALITY_CAPABILITY_P2_RC1_FULL.zip`
- 两个 ZIP 均附带 `SHA256SUMS.txt`，用于核对文件完整性。
