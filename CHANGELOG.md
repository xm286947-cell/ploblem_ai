# CHANGELOG

## V1.0 M7.1

### 优化

- 检索排序从问题描述主导升级为综合相似性判定
- 新增原因描述相似度
- 提升原因一级、二级分类权重
- 新增解决措施相似度
- 保留IPMT、SPDT和责任部门组织相似度
- 输入字段缺失时动态归一化权重，不进行机械扣分
- 输出问题、原因、分类、措施、组织五类分项得分
- 新增`--cause-description`与`--solution`参数

## V1.0 M7

### 新增

- 新增相似历史案例Retriever
- 支持新问题文本查询
- 支持IPMT、SPDT、责任部门、产品、领域和原因分类条件
- 支持向量召回
- 支持关键词、标签、组织、原因分类规则重排
- 输出综合得分及分项得分
- 输出推荐理由、共同关键词和共同标签
- 输出JSON、Markdown和CSV结果
- 新增`config/retrieval.yaml`
- 新增`python main.py run-m7`

## V1.0 M6 Fix-02

### 修复

- 修复部分Excel导出文件工作表维度元数据错误，导致只读取A列的问题
- Excel解析不再依赖`worksheet.max_column`
- 表头扫描固定扩展到安全列范围
- 数据读取使用识别到的表头宽度和安全列范围
- 工作簿改为普通读取模式，兼容异常Dimension信息

## V1.0 M6 Fix-01

### 修复

- 修复`sheet_name=null`时固定读取第一个Sheet的问题
- 自动扫描所有Sheet并选择有效数据Sheet
- 自动扫描前20行识别表头
- 兼容表头尾部空格、换行、中文括号和英文括号
- 增加列名别名映射
- 修正Excel真实行号记录
- 数据行数为0时不再静默成功，改为明确报错

### 日志增强

- 输出最终Sheet和表头行
- 输出实际识别表头
- 输出所有Sheet扫描诊断
- 输出缺失列清单

## V1.0 M6

### 新增

- Retrieval Document Builder
- 结构化过滤字段
- Local Hash离线Embedding
- OpenAI兼容Embedding接口
- 每案例Embedding文件
- `case_index.jsonl`
- `knowledge_manifest.json`
- M6三类JSON Schema
- `run-m6`
- `run-all --with-index`
- M6执行摘要与失败日志

### 数据边界

- M6只读取`enriched_case`
- 不修改Standard Case或Enriched Case
- 检索文档、向量、索引和Manifest独立存储
- 每个检索文档使用内容Hash支持后续增量更新

## V1.0 M5

### 新增

- 独立AI Enricher
- OpenAI兼容接口客户端
- Mock验证模式
- AI响应JSON Schema
- Prompt配置目录
- `run-m5`
- `run-all --with-ai`
- `run-all --mock-ai`
- `knowledge/enriched_case`
- AI失败日志和M5摘要

### 保护规则

- 不覆盖`standard_case`
- 不修改original/report事实
- 不修改证据引用
- AI输出Schema校验后才能落盘
- 已有增强结果默认跳过，需`--overwrite`才覆盖

## V1.0 M4

### 新增

- Evidence Fusion
- 纯事实 Standard Case 构建
- `run-m4`
- `run-all`
- `knowledge/enriched_case` 预留目录
- 用户型 README
- `docs/` 文档体系
- Fusion Schema 校验
- M4执行摘要与失败日志

### 融合规则

- Excel、PDF事实分层保留
- AI与标准化字段不在M4生成
- TRC、MRC保留original/report/standard结构
- PDF Section保留证据来源
- 缺失报告、根因、预防措施生成质量标记

### 优化

- 支持通过命令行直接指定真实Excel和PDF目录
- 支持M2到M4一键执行
- 更新工程版本为`1.0.0-m4`

## V1.0 M3

- PDF文本和表格提取
- Evidence Block与Section识别
- 跨页Section合并
- 扫描件疑似标记

## V1.0 M2

- Excel Parser
- 报告文件匹配
- Raw Layer

## V1.0 M1

- Standard Case Schema
- 基础配置和校验器

## V2.0 M7.1 Query Excel Parser & Raw Query

### 新增

- 新增`config/query_field_mapping.yaml`，待分析问题字段映射完全配置化
- 新增`parser/query_excel_parser.py`
- 支持单表头、双表头、自动Sheet与表头识别
- 支持异常Dimension工作簿的64列安全扫描
- 支持原因一级至四级分类
- 保留原始列名、原始值、Sheet和Excel真实行号
- 必填校验：查询编号、问题描述
- 单行失败隔离，不中断批量解析
- 新增`schema/raw_query.schema.json`
- 新增`knowledge/raw_query/{query_id}.json`
- 新增`output/logs/query_parse_summary.json`
- 新增CLI：`python main.py run-m7-query --input input/new_cases.xlsx`
- 新增M7.1单元测试与示例Excel

### 边界

- 本里程碑不调用AI
- 不执行检索、相似性分析、重复判断和报告生成
- 不修改M2至M7历史案例与检索产物

## V2.0 M7.2.1
- 新增确定性规则层 `builder/query_normalizer.py`。
- 新增 `config/query_normalization.yaml`，支持空值、Boolean、List、日期、产品、组织与原因分类标准化。
- 新增 `schema/normalized_query.schema.json`。
- 新增批量运行器与 CLI：`run-m72-normalize`。
- 新增 Normalizer 单元测试，确保原始值不被修改、规则可追溯及输出可重复。

## V2.0 M7.2.3

- 新增 Query AI Enricher。
- 新增 Enriched Query Schema。
- 新增 `run-m72-ai` CLI，支持单条、批量、Mock、跳过AI和覆盖重跑。
- 增加 original/normalized 只读保护、AI输出校验和失败降级。

## V2.0 M7.2.5

- 新增 Standard Query Builder。
- 新增 Standard Query JSON Schema。
- 实现 original / normalized / inferred / effective / effective_source 分层组装。
- 实现事实类、分析类与混合类字段选值规则。
- 实现完整度、质量标记、lineage 与 AI 失败降级构建。
- 新增 `run-m72-build` CLI 与单元测试。

## V2.0 M7.2.6 / M7.2.7

- Added `builder/query_enricher.py` as the M7.2 orchestration-only pipeline.
- Added `run-m72` CLI for full, single-query, degraded, and stage-specific reruns.
- Added persisted pipeline summary and per-query status aggregation.
- Added end-to-end tests covering full pipeline, AI skip degradation, Builder-only rerun, and missing-query isolation.

## V2.0 M7.3.3

- 新增 Retrieval Profile Schema。
- 新增配置驱动的 Retrieval Profile Builder。
- 支持 Filter / High Weight / Medium Weight / Low Weight 字段分组。
- 支持 HARD / SOFT / PREFER / EXPANDABLE 过滤模式表达。
- 权重综合字段来源、AI置信度与完整度计算。
- 新增不同产品 Profile 覆盖接口。
- 新增 Keyword、BM25、Embedding、LLM Rerank 通道字段视图。
- 新增 Retrieval Profile 到现有 QueryInput 的兼容适配器。
- 新增单条和批量 CLI。

## V2.0 M8.1

- 新增 Candidate Loader 与 Analysis Context。
- 新增 `run-m81-load` CLI。
- 支持按 Query、Top-K 批量聚合候选案例证据。
- 支持案例证据缺失降级及完整追溯。

## V2.0 M8.2

- 新增多维 Similarity Analyzer。
- 新增证据驱动的维度评分与综合相似度输出。
- 新增 Mock、Skip-AI、单 Query、单 Candidate 和批量运行能力。
- 新增 Similarity Analysis Schema、Prompt、自动化测试和汇总日志。

## V2.0 M8.3

- 新增 Solution Analyzer，分析历史纠正措施、预防措施、验证证据和闭环状态。
- 区分措施有效性与当前问题复用适用性。
- 支持 DIRECT_REUSE、PARTIAL_REUSE、REFERENCE_ONLY、NOT_APPLICABLE 和 UNKNOWN。
- 新增 M8.2 相似性结果引用；缺失时允许降级分析并产生质量警告。
- 新增 `run-m83-solution` CLI、Schema、Prompt、Mock、自动化测试与汇总日志。

## V2.0 M8.4

- 新增 Repeat Decision Engine。
- 新增统一 `RepeatAnalysis` 输出，供 M8.5、M8.6 和后续 RC 阶段直接消费。
- 新增候选级证据链、重复判定和最终排序。
- 新增 `run-m84-decision` CLI、Schema、Prompt、配置和自动化测试。

## V2.1 Sprint-2 Patch-01

- 新增文件型 Report Repository。
- Presentation 输出路径与序列化职责从 M8.4 Runner 中剥离。
- Report Repository 统一持久化 `report.json` 与 `report.md`。
- 增加原子 JSON 写入、Report 读取与 Query ID 路径安全校验。
- M8.4 分析逻辑及原有 `repeat_analysis.json` 输出保持不变。

## V2.1 Sprint-2 Patch-03

- Added configurable department candidate filter.
- Added comparison context to repeat analysis candidate output.
- Upgraded Report Contract to V2.1 for human repeat-case confirmation.
- Rebuilt Markdown report around reason classification, root cause, measures, evidence, checklist, and risks.
- Removed internal ranking and scoring fields from report.md.
- Added CandidateFilter, ReportBuilder V2.1, and MarkdownRenderer V2.1 tests.

## V2.3 M2 RC1

- Centralized safe JSON artifact access in `JsonArtifactRepository`.
- Added `KnowledgeService` for query and case knowledge loading.
- Migrated M8.1 candidate context assembly away from duplicated direct file reads.
- Preserved existing schemas, CLI and output layout.
- Added repository and knowledge-service regression tests.

## V2.3 M2 RC2 - Evidence Schema Migration

- 修复 Query AI Enricher 的 Prompt/Schema 字段形态不一致。
- 新增统一 Evidence Schema 迁移层。
- keywords、tags、overall_confidence 升级为 Evidence Object。
- operating_context 等数组字段支持单对象兼容迁移。
- 保持 Standard Query 下游交付结构兼容。
- 全量测试 98 passed。

## V2.3 M3 RC1

- Centralized M8.2-M8.4 analysis artifact access in `KnowledgeService`.
- Removed duplicated direct JSON reads from similarity, solution and repeat-decision runners.
- Routed similarity, solution and repeat-analysis persistence through `JsonArtifactRepository`.
- Added analysis artifact access and case-filter regression tests.
- Full regression: 100 passed.

## V2.3 M4 RC1

- 新增新问题分析端到端 Pipeline：`run-analysis`。
- 支持阶段状态、耗时、失败隔离和断点续跑。
- 支持单 Query 与批量 Query。
- 新增 `analysis_pipeline_summary.json`。
- 保持现有 Stage Runner、Prompt、Schema、算法和输出目录不变。

## V2.3 M5 RC1

- 新增 `DeliveryService`，建立 `REPEAT_CASE_REPORT` V1.0 Delivery Contract。
- 新增 `run-m85-delivery`，从 RepeatAnalysis 生成正式 Report JSON 与 Markdown。
- 新增 `output/reports/report_index.json` 与 `report_index.md`。
- `run-analysis` 新增 delivery 阶段并支持 `--from-stage delivery`。
- 新增 M8.5 Delivery 测试，完整回归 106 passed。

## V2.3 M5 RC2

- 恢复 M8.2、M8.3 候选案例 AI 多线程执行。
- 新增统一 `parallel_ai` 配置和有序并发执行器。
- 增加并发执行状态、工作线程数到阶段 Summary。
- 全量回归 109 passed。

## V2.3 M5 RC3 PATCH01
- 修复真实嵌套数据结构下原因分类读取失败。
- 补充历史案例原因三级、四级分类导入与Schema兼容。
- 新增真实结构单元测试。

## V2.3 M5 RC3 PATCH02
- 根因对比输出改为业务化结构，补充根因分析结论。
- 措施对比输出改为当前/历史/纠正/预防/共同/差异/建议补充/结论。
- 清除措施对比中的内部英文键泄漏。
- 新增根因与措施对比回归测试。

## V2.3 M5 RC3 PATCH03

- 修复 M7.3 Retrieval Profile 在嵌套 Evidence 对象场景下的 Schema 校验失败。
- Retrieval Profile `values` 统一转换为字符串、数字或布尔值。

## M5 RC3 PATCH04
- 修复 AI 修正后的原因分类未进入 effective 字段的问题。
- 原因分类改为优先使用 INFERRED，AI 为空时回退 NORMALIZED/ORIGINAL。

## V2.3 M5 RC3 PATCH05

- 同一 Query ID 重新运行前自动清理该 Query 的历史中间产物，避免旧文件复用和覆盖冲突。
- ALL 模式以 `knowledge/raw_query` 为唯一有效 Query 清单，并清理下游孤儿 Query 产物。
- 综合置信度在判定置信度为空或为 0 时，回退使用相似度分析置信度、综合相似度或有效维度平均分。
- 推荐案例及其他候选案例展示综合相似度和各相似维度得分。
- 缺失评分显示“未评分”，不再自动显示为 0。

## V2.3 M5 RC3 PATCH06

- 解耦 Similarity / Confidence / Context Filter / Recommendation。
- 组织上下文从相似度评分中移除，仅用于筛选和适用性说明。
- 综合相似度忽略未评分维度，判断置信度按证据质量独立计算。
- 报告新增置信度依据、组织适用性、推荐等级及各候选评分说明。

## V2.4 M6 Local Batch Engine

- 新增 `run-batch` 本地批量运行命令。
- 新增 Run Manifest、逐 Query 状态、批次汇总与失败清单。
- 新增断点续跑和仅重跑失败 Query。
- 每个 Query 运行前自动清理 Query 级历史产物，降低同 ID 冲突风险。
- 单 Query 失败不阻断整批任务。
- 默认不导出敏感中间数据；敏感 Debug Bundle 需显式开启。

## V2.4 M6 Solution Context Optimization
- Slimmed M8.3 AI payload to remove duplicate/raw retrieval content.
- Added similarity-based Top-N candidate selection before Solution Analysis.
- Added independent solution_ai timeout/token/retry controls with common AI inheritance.
- Added M8.3 prompt-size diagnostics and reduction metric.
- One-click analyze.py `all` path now includes M8.5 Delivery.
