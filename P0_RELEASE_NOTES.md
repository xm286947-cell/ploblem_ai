# Quality Capability P0 RC1

## IMPLEMENTATION_STATUS

P0 已闭环：全新 SQLite 初始化、PLC 标准字段基线、动态产品与 Mapping、Preview-before-Commit、严格 V2 四阶段分析、人工确认、双轴质量洞察、问题工作台和问题详情。

## DATABASE / CONTRACT CHANGES

- 使用全新 P0 Schema；旧验证数据库只允许字节级备份，不读取、不迁移。
- PLC 基线：60 个定义、59 个业务字段、111 个 Alias；SourceID 为 RAW_ONLY。
- 仅 `ISSUE_FACT.business_issue_id` 必填。
- 人工确认区分 PENDING、CONFIRMED、CORRECTED、UNRESOLVED、NOT_APPLICABLE；只有有效确认或修正覆盖洞察。
- 新产品 Mapping 从 ACTIVE 标准字段目录生成 59 字段 Starter Draft，不复制其他产品源表头。

## PRIMARY ENTRY POINTS

- `/p0/insights`：质量洞察。
- `/p0/issues`：问题工作台。
- `/p0/settings`：产品与字段映射。
- `/p0/data-intake`：数据接入。

## STARTUP

```bash
python main.py knowledge-p0-init --db ./quality_p0.db
python main.py knowledge-p0-web --db ./quality_p0.db --host 127.0.0.1 --port 8080
```

## TEST RESULT

- P0 核心专项：74 passed。
- 工程全量回归：341 passed。
- 浏览器验收：产品新增、59 字段 Draft、Save、Validate、Activate、Preview 阻断与缺失行诊断通过。
- JavaScript 语法检查：通过。

## COMPATIBILITY

本版本不承诺旧验证数据库兼容；旧版本主要用于验证。P0 使用独立新库，不修改 `sources/` 和 P2 基线。
