# M7.1 Query Excel Parser & Raw Query

## 目标

将`input/new_cases.xlsx`逐行转换为可追溯的Raw Query JSON。本阶段只处理输入，不调用AI。

## 运行

```bash
python main.py run-m7-query --input input/new_cases.xlsx --overwrite
```

## 输入字段

必填：查询编号、问题描述。

推荐/可选：ITR单号、IPMT、SPDT、责任部门（二级）、产品、领域、原因描述、原因一级至四级分类、纠正措施、预防措施、备注。

字段别名在`config/query_field_mapping.yaml`维护。

## 状态

- `SUCCESS`：必填字段完整且无解析警告
- `PARTIAL_SUCCESS`：必填字段完整，但存在可选列缺失等警告
- `QUERY_PARSE_FAILED`：查询编号或问题描述缺失，或查询编号重复

失败记录以`FAILED-ROW-{excel_row}.json`保存，批量任务继续执行。

## 输出

- `knowledge/raw_query/{query_id}.json`
- `output/logs/query_parse_summary.json`
