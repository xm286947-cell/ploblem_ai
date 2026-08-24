# REPEAT_CASE_ENGINE V2.3 M5 RC3 PATCH03

## 修复内容

- 修复 Retrieval Profile 的 `values` 数组写入 Evidence 对象导致 Schema 校验失败。
- 支持解包 `effective.value`、`value`、`normalized`、`original` 等包装结构。
- 保持 Retrieval Profile 合同不变，`values` 仅允许 string / number / boolean。
- 新增嵌套 Evidence 对象回归测试。
