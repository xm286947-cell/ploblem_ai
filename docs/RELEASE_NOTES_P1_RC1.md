# Quality Capability P1 RC1 Release Notes

发布日期：2026-08-24

## 启动入口

首次使用或全新数据库：

```text
python main.py knowledge-p1-start
```

已初始化数据库：

```text
python main.py knowledge-p1-web
```

默认地址：`http://127.0.0.1:8080/`

## 本次闭环

- 使用 PLC YAML 作为标准字段基线，不依赖旧数据库。
- 新产品无 ACTIVE Mapping 时可基于 Starter Draft 预检。
- Excel 差异支持映射已有字段、仅保留 Raw、暂不处理。
- Draft 必须 Validate / Activate 后重新 Preview，才允许正式导入。
- 字段选择支持中文名、目标路径和检索。
- 默认并发 2，可配置 1–4 个问题同时进行四阶段 AI 分析。
- AI 运行环境未就绪时，工作台直接显示原因并阻止提交。
- 批量分析区分成功、部分完成和失败，不再将全阶段失败计为成功。
- 人工确认独立保存，不覆盖 AI 原始结论。
- 质量洞察支持工程/管理双维度聚合及精确问题下钻。
- 历史问题可发布为风险案例，并用于需求、设计、测试和发布材料的正向风险评估。

## 数据与契约

- 不迁移、不读取、不兼容旧业务数据库。
- `source_id` 不是必填字段。
- 数据库 Schema 与 P0/P1 V2 契约保持不变。
- 新增 HTTP 接口：`GET /api/v2/analysis-runtime/status`。
- 批量分析响应新增 `partial` 计数，并将单问题结果明确标记为 `SUCCEEDED`、`PARTIAL` 或 `FAILED`。

## E2E 验收结果

- YAML 全字段样例：59 个字段、5 条典型 PLC 问题。
- Preview：59 个表头、5 行、0 冲突、0 必填缺失。
- Import：5 成功、0 失败、0 重复。
- AI 分析：并发 2，5 成功、0 部分、0 失败；共保存 20 条阶段运行。
- 人工确认：Revision 1 生效，AI 原始结论保持不变。
- 洞察：5/5 问题完成分析，分类覆盖率 100%，精确下钻返回 5 条关联问题。
- 正向风险：发布 1 条风险案例，设计材料匹配 1 条风险，控制覆盖为 `COVERED`。
- P0/P1 相关回归：112 passed，0 failed。

## 验收样例

`outputs/quality_capability_p1/plc_quality_issue_full_fields_e2e.xlsx`

