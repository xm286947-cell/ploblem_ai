# REPEAT_CASE_ENGINE V2.3 M5 RC3 PATCH01

## 修复内容

1. 修复 ReportBuilder 无法从真实嵌套结构读取原因分类的问题。
2. 支持 standard_query.classification.cause_level1~4 的 effective/original 值。
3. 支持 standard_case/enriched_case.classification.original.cause_level1~4。
4. Excel 历史案例导入补充原因三级分类、原因四级分类映射。
5. Standard Case Schema 兼容原因三级分类、原因四级分类。

## 测试结果

112 passed
