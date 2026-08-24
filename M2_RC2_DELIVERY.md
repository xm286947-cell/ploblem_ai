# REPEAT_CASE_ENGINE V2.3 M2 RC2 Delivery

## 主题
Evidence Schema Migration

## 修复问题
修复 Query AI Enricher 中 Prompt 输出 Evidence Object，而 Schema 仍按 primitive 校验导致的批量 schema_errors。

## 主要变更
- `keywords`、`tags` 统一为 Evidence Object 数组。
- `overall_confidence` 统一为 Evidence Object。
- `operating_context` 等数组字段支持模型误输出单对象时自动迁移为数组。
- 新增 `evidence_schema_migration.py`，在 Schema 校验前统一字段形态。
- 缺失必要顶层字段时仍判定为 `AI_OUTPUT_INVALID`，避免无条件补全掩盖无效输出。
- Standard Query Builder 在下游交付时继续输出字符串数组和数值置信度，保持现有业务结构兼容。
- Legacy M7 Adapter 支持旧版数值型 `overall_confidence` 自动迁移。
- Prompt 明确约束 keywords、tags、overall_confidence、operating_context 的输出形态。

## 验证结果
- 全量自动化测试：98 passed
- 已覆盖截图中同类混合形态：
  - Evidence Object keywords/tags
  - Evidence Object overall_confidence
  - 单对象 operating_context
- 旧版 primitive 输出兼容验证通过。

## 影响范围
仅影响 Query AI Enricher 的 Evidence 数据契约、兼容迁移及 Standard Query Builder 的取值适配。

## 未修改
- Retrieval
- Similarity
- Repeat Decision
- Report/Renderer
- 外部 CLI 参数
