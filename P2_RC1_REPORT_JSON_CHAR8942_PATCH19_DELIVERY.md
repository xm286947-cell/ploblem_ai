# PATCH19 产品报告 JSON Char 8942 修复

## 根因判断

- 本地 OpenAICompatibleClient 使用 response.read() 读取完整 HTTP 响应，没有 8942 字符截断限制。
- `Char 8942` 是 JSONDecodeError 报告的语法错误位置，不等于响应只读取了 8942 字符。
- 长报告更容易产生未闭合 JSON、未转义引号或中途停止，因此原直接综合阈值不够稳妥。

## 修复

- 默认批次从25降到10；14个问题自动执行2次 BATCH_MAP 和1次 FINAL_REDUCE。
- 可通过 PRODUCT_REPORT_BATCH_SIZE 调整，安全范围5～25。
- 报告专用 max_tokens 默认至少8192，也可通过 model.yaml 的 product_report_max_tokens 设置。
- Prompt 限制每个数组最多5项、每个文字字段不超过120字。
- 首次JSON解析失败时自动用精简约束完整重试一次。
- 两次均失败时错误包含响应字符数与 finish_reason。
- 每次完整原始响应和元数据保存到 output/raw_ai/product_report/，元数据包含 output_chars、finish_reason、usage。

## 验证

- 39305 total_tokens 元数据保留专项通过。
- 首次损坏、第二次有效JSON自动恢复专项通过。
- 全量回归：395 passed。
