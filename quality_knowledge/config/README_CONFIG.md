# Quality Issue Field Config V1.0

## 目的

将 HMI / PLC / IFA 的字段识别、字段别名和映射从 Python 代码中抽离为配置，便于人工维护。

## 配置原则

- Engine 代码负责：Excel Loader、Header Normalizer、Adapter Engine、Version、Repository。
- YAML 负责：业务识别字段、字段别名、字段映射、产品差异字段。
- Raw Header / Raw Row 永远保留原文。
- Normalized Header 只用于匹配，不覆盖原始数据。
- 未映射字段仍进入 raw_json，不允许丢失。

## 文件

- import.yaml：全局导入与 Header 标准化规则。
- plc_fields.yaml：PLC 字段映射。已结合当前真实 PLC Excel 表头增强。
- hmi_fields.yaml：HMI 初版，来源于冻结 Design，需后续真实 Excel 验证。
- ifa_fields.yaml：IFA 初版，来源于冻结 Design，需后续真实 Excel 验证。

## 建议 Engine 后续支持

1. `knowledge-config-check`
2. `knowledge-import-preview --input xxx.xlsx`
3. 输出 matched / unmapped / ambiguous 字段。
4. 配置文件修改后无需修改 Python Adapter 代码。
