# REPEAT_CASE_ENGINE V2.3 M5 RC3 PATCH04

## 修复内容

修复 AI 修正后的原因分类未被用于后续检索与报告的问题。

原因分类 effective 值的优先级调整为：

AI 修正值（INFERRED） → 规范化原值（NORMALIZED） → 原始值（ORIGINAL）。

仅影响原因一级至四级分类；其他事实字段仍保持原优先级。

## 测试

115 passed
