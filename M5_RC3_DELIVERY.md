# REPEAT_CASE_ENGINE V2.3 M5 RC3

## 修复内容

1. 原因分类字段统一为：原因一级分类、原因二级分类、原因三级分类、原因四级分类。
2. 兼容历史输入字段：一级原因分类、二级原因分类、三级原因分类、四级原因分类。
3. Markdown 不再输出 Evidence/DTO 内部字段：value、original、normalized、inferred、source_type、confidence。
4. 空值统一显示为“未提供”。
5. 新增 RC3 回归测试。

## 测试结果

111 passed。
