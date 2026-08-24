# Quality Capability P1 RC1

## IMPLEMENTATION_STATUS

P1 已闭环：确认后的问题可发布为版本化风险案例；同类问题可合并为风险模式；支持结构化与关键词检索；需求、设计、测试、发布材料可执行正向质量风险评估、控制覆盖检查、人工复核和材料更新重评。

## RISK CASE RESULT

- 风险案例记录内容 Hash、来源 Analysis Set、Taxonomy Version、版本号和关联问题。
- 发布级别：`INTERNAL_FULL`、`INTERNAL_REDACTED`、`EXTERNAL_PATTERN`、`DO_NOT_PUBLISH`。
- 外部风险模式不输出原始问题编号、标题、客户影响、产品版本、措施原文或证据原文。
- 支持按关键词、产品、领域、生命周期、MRC、能力缺口和发布级别检索。
- 多个问题可合并为一个风险模式，新版本保留全部关联关系。

## FORWARD ASSESSMENT RESULT

- 阶段：`REQUIREMENT`、`DESIGN`、`TEST`、`RELEASE`。
- 覆盖状态：`COVERED`、`PARTIAL`、`NOT_FOUND`、`INSUFFICIENT_INFO`、`NOT_APPLICABLE`。
- 报告展示匹配依据、适用边界、触发条件、客户影响、已有/缺失控制、工程/管理措施、验证场景、待确认事项和置信度。
- 人工复核独立保存；全部风险复核后评估状态转为 `COMPLETED`。
- 材料更新生成新版本，展示新增风险、消除风险、等级变化和控制覆盖变化。
- 否定表达受保护，例如“尚未建立兼容性矩阵”不会被误判为已覆盖。

## DATABASE / CONTRACT CHANGES

- 数据库 Schema：`2.1.0`。
- 新增风险案例、案例版本、问题关联、正向评估、评估版本和风险结果表。
- P0 四阶段 AI 分析输出契约继续保持 `contract_version = 2.0.0`。
- 本版本使用全新 P1 数据库初始化，不承诺旧验证数据库兼容。

## PRIMARY ENTRY POINT

- `/p1/risk-assessment`：风险案例库与正向质量风险评估。
- P0 质量洞察、问题工作台、数据接入和系统设置均增加 P1 入口。

## TEST RESULT

- P1 专项与页面契约：17 passed。
- 工程全量回归：358 passed，0 failed。
- JavaScript 语法检查：通过。
- 浏览器验收：案例展示、动态产品、首次评估、否定控制、材料重评、V1→V2 差异和业务化匹配依据通过。

## STARTUP

```bash
python main.py knowledge-p0-init --db ./quality_p1.db
python main.py knowledge-p0-web --db ./quality_p1.db --host 127.0.0.1 --port 8080
```

访问 `http://127.0.0.1:8080/p1/risk-assessment`。
