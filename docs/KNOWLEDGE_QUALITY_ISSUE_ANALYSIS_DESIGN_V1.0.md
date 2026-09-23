# KNOWLEDGE_QUALITY_ISSUE_ANALYSIS_DESIGN_V1.0

**Status:** Design Baseline / Engine Input Candidate  
**Input Requirements:** `KNOWLEDGE_QUALITY_ISSUE_ANALYSIS_REQUIREMENTS_V1.0`  
**Existing Baseline:** `REPEAT_CASE_ENGINE_V2.4_M6_SOLUTION_OPTIMIZED`  
**Scope:** Knowledge Capability Extension

---

## 1. Design Goal

在不修改现有 Repeat Case 相似度、Solution Analysis、Repeat Decision 业务逻辑的前提下，扩展现有 Knowledge Capability，使 HMI、PLC、IFA 历史质量问题能够：

1. 保留完整原始数据；
2. 形成跨产品统一的结构化问题知识；
3. 保留产品差异字段；
4. 分离“问题发生原因 / 问题流出原因 / 产品与特性归属”三类知识；
5. 基于 AI 派生“再发防控能力缺口”；
6. 同时识别技术能力、管理能力、横向治理能力；
7. 通过 SQLite 支持后续查询、统计和 Excel/CSV 导出；
8. 保持 AI 分析结果证据、Prompt、模型和版本可追溯。

本设计不建设独立 Knowledge Platform，不修改 Repeat Case 现有判定逻辑。

---

## 2. Existing Capability Assessment

现有基线已具备以下可直接复用能力：

### 2.1 数据分层

现有架构已经明确：

```text
Raw Layer
    ↓
Fact / Standard Case Layer
    ↓
AI Enriched Layer
```

并已有硬约束：

> Raw Layer、事实层和 AI 增强层不得相互覆盖。

该约束继续作为本设计的核心数据治理原则。

### 2.2 Evidence Model

现有：

- `EvidenceField[T]`
- `EvidenceReference`
- `EvidenceType`
- confidence
- source reference
- reason

可直接复用于所有 AI 派生知识。

### 2.3 Knowledge Access Boundary

现有：

```text
Business Logic
      ↓
KnowledgeService
      ↓
JsonArtifactRepository
```

说明上层业务已经不需要感知具体文件布局。

本次继续保持这个边界，并新增 SQLite Repository / Query Repository，而不是让业务代码直接访问 SQLite。

### 2.4 Existing Knowledge Objects

现有 `KnowledgeCase` 已包含：

- problem_description
- feature
- phenomena
- trigger_conditions
- failure_mechanisms
- root_causes
- keywords
- tags

现有 Standard Case 已包含：

- problem
- analysis
- classification
- solution
- knowledge

其中已经具备：

- TRC occurrence
- TRC escape
- MRC occurrence
- MRC escape
- root cause
- contributing factors
- management actions
- technical actions
- reusable actions

因此本需求不是重新创建根因模型，而是在现有模型上增加：

```text
Product-neutral Issue Knowledge
        +
Escape Knowledge
        +
Prevention Capability Knowledge
        +
Cross-product Query Model
```

---

## 3. Target Architecture

```text
 HMI Excel        PLC Excel        IFA Excel
     \                |               /
      \               |              /
       +------ Source Adapter -------+
                     |
                     v
              Raw Issue Record
              原始字段100%保留
                     |
                     v
              Issue Normalizer
                     |
                     v
         Common Issue Fact Model
                     |
          +----------+-----------+
          |                      |
          v                      v
 Product Extension         Evidence Mapping
 产品差异字段                  来源证据
          |                      |
          +----------+-----------+
                     |
                     v
             AI Issue Analyzer
                     |
      +--------------+-------------------+
      |              |                   |
      v              v                   v
 Occurrence       Escape            Prevention
 Analysis         Analysis          Capability Gap
      |              |                   |
      +--------------+-------------------+
                     |
                     v
           Structured Knowledge Store
                  SQLite
                     |
       +-------------+--------------+
       |             |              |
       v             v              v
   Query API     Statistics     Export
       |
       v
 Retrieval Capability / Business Capability
```

---

## 4. Core Domain Model

本次不建议把所有内容继续塞进单一 `standard_case` JSON。

新增领域对象：

```text
QualityIssueKnowledge
```

它是跨 HMI / PLC / IFA 的公共知识对象。

### 4.1 QualityIssueKnowledge

```text
QualityIssueKnowledge
├── identity
├── source
├── issue_fact
├── product_context
├── occurrence
├── escape
├── solution
├── prevention
├── classification
├── product_extension
└── metadata
```

---

## 5. Knowledge Object Definition

### 5.1 Identity

```text
knowledge_id
case_id
business_type        HMI / PLC / IFA
issue_id             TRC / ITR
source_record_id
```

要求：

- `knowledge_id` 为 Knowledge 内部稳定 ID；
- `issue_id` 保留业务原始单号；
- 不以 ITR 单号作为数据库主键，避免跨业务冲突。

---

### 5.2 Source

```text
source_file
source_sheet
source_row
source_import_batch
raw_record_ref
```

作用：

- 可从任意派生知识回到原始 Excel 行；
- 支撑数据追溯和重新分析。

---

### 5.3 Issue Fact

统一公共问题事实：

```text
title
description
impact
severity
issue_type
is_defect
month
industry
customer
department
business_group
platform
product
module
```

字段允许为空。

原则：

> 只保存能够从原始数据直接得到的事实，不允许 AI 覆盖。

---

### 5.4 Product Context

统一 Where 维度：

```text
product
product_series
platform
module
business_group
feature_l1
feature_l2
feature_l3
feature_l4
fa_feature
fa_l1_feature
layer
layer_category
```

这部分回答：

> 问题发生在哪里？

不得与原因分类合并。

---

## 6. Three Independent Classification Dimensions

设计冻结：

### 6.1 Product / Feature Classification — Where

```text
Product
 → Platform
 → Feature
 → Module
 → Layer
```

### 6.2 Occurrence Cause Classification — Why Occurred

```text
Occurrence Cause
 → Level 1
 → Level 2
 → Level 3
 → Level 4
```

### 6.3 Escape Cause Classification — Why Escaped

```text
Escape Cause
 → Level 1
 → Level 2
 → Level 3
 → Level 4
```

三棵树必须独立存储。

禁止：

```text
classification_level1
classification_level2
classification_level3
classification_level4
```

这种无法知道语义的公共字段设计。

必须显式：

```text
occurrence_cause_l1
escape_cause_l1
feature_l1
```

---

## 7. Product Field Mapping Strategy

采用：

```text
Raw Field
    ↓
Product Adapter
    ↓
Canonical Field
    ↓
Product Extension
```

而不是：

```text
三张Excel
 ↓
强行统一列名
```

---

## 8. HMI Mapping

### 公共字段候选

```text
TRC单号               → issue_id
问题描述               → description
软件问题月份           → month
所属部门               → department
平台                   → platform
产品分类               → product
产品系列               → product_series
模块                   → module
是否宕机问题           → product_extension.is_crash
根因分析               → occurrence.root_cause
纠正措施               → solution.corrective_action
改进措施               → solution.improvement_action
是否漏测               → escape.is_escape
流出根因说明           → escape.root_cause
流出改进措施           → escape.improvement_action
```

### 分类规则

HMI：

```text
一级分类～四级分类
```

进入：

```text
occurrence.classification
```

HMI：

```text
流出原因分类
```

进入：

```text
escape.classification
```

---

## 9. PLC Mapping

PLC 必须显式区分两套分类。

### 9.1 问题分类

```text
新二级分类
新三级分类
新四级分类
```

属于：

```text
occurrence / issue classification
```

并与 HMI 问题分类体系对应。

### 9.2 漏测/流出分类

```text
原因分类一级
原因分类二级
原因分类三级
原因分类四级
```

属于：

```text
escape.classification
```

### 9.3 重要公共字段

```text
问题原因定位              → occurrence.root_cause
问题解决方案              → solution.original_solution
开发改进措施              → solution.technical_or_dev_action
管理改进措施              → solution.management_action
技术改进措施              → solution.technical_action
是否漏测                  → escape.is_escape
漏测根因补充说明          → escape.root_cause
纵向影响域                → prevention.vertical_scope
横向影响域                → prevention.horizontal_scope
是否变更引入              → product_extension.change_introduced
变更原因                  → product_extension.change_reason
变更影响点                → product_extension.change_impact
是否已有用例              → verification.existing_case
是否为必测项              → verification.mandatory_test
是否已自动化              → verification.automated
必测项未自动              → verification.mandatory_not_automated
是否场景问题              → scenario.is_scenario_issue
是否可以通过场景调研覆盖  → scenario.research_coverable
```

---

## 10. IFA Mapping

```text
ITR单号                  → issue_id
行业                     → industry
客户                     → customer
问题类型                 → issue_type
严重程度                 → severity
Bug标题                  → title
问题描述                 → description
Bug所属业务组            → business_group
影响结果                 → impact
是否为缺陷               → is_defect
问题原因定位             → occurrence.root_cause
问题解决方案             → solution.original_solution
是否漏测                 → escape.is_escape
漏测类型                 → escape.escape_type
流出原因补充描述         → escape.root_cause
改进点                   → solution.improvement_action
提炼测试场景             → verification.extracted_test_scenario
历史同类问题             → repeat_history.description
是否历史同类ITR问题      → repeat_history.is_repeat
一级分类～四级分类       → occurrence.classification
根因分析                 → occurrence.root_cause_evidence
纠正措施                 → solution.corrective_action
改进措施                 → solution.improvement_action
```

---

## 11. Occurrence Knowledge

```text
OccurrenceKnowledge
├── original_reason
├── root_cause
├── contributing_factors
├── failure_mechanism
├── classification
└── evidence
```

回答：

> 为什么问题会发生？

必须区分：

```text
Fact
AI Derived
```

例如：

```text
root_cause_original
root_cause_normalized
root_cause_ai
```

AI 不得回写原字段。

---

## 12. Escape Knowledge

```text
EscapeKnowledge
├── is_escape
├── escape_type
├── original_reason
├── root_cause
├── classification
├── verification_gap
└── evidence
```

回答：

> 为什么问题没有在研发阶段被拦截？

可能涉及：

- 用例缺失
- 场景覆盖不足
- 必测项缺失
- 自动化缺失
- 测试环境不足
- 评审未识别
- 变更影响分析不足
- 准入机制不足

这类内容不能和问题发生原因放在同一个 cause tree 中。

---

## 13. Prevention Capability Knowledge

这是本次设计的核心新增对象。

```text
PreventionKnowledge
├── recurrence_risk
├── technical_capability_gaps[]
├── management_capability_gaps[]
├── governance_capability_gaps[]
├── recommended_controls[]
├── horizontal_scope
├── vertical_scope
└── evidence
```

目标不是生成一句“改进建议”。

而是回答：

```text
同类问题为什么可能再次发生？
       ↓
目前防控体系缺什么？
       ↓
应该补什么能力？
```

---

## 14. Capability Gap Model

### 14.1 Technical Capability Gap

第一版候选维度，不在 Requirements 层冻结，但 Design 建议以此作为 V1 taxonomy：

```text
TECH_METHOD
TECH_TOOL
TEST_CAPABILITY
AUTOMATION
OBSERVABILITY
METRIC
TECH_STANDARD
DESIGN_GUARDRAIL
STATIC_ANALYSIS
TEST_ENVIRONMENT
TEST_DATA
```

### 14.2 Management Capability Gap

```text
PROCESS
REVIEW
CHANGE_MANAGEMENT
ENTRY_EXIT_CRITERIA
MANDATORY_TEST
ISSUE_CLOSURE
TRAINING
ROLE_RESPONSIBILITY
QUALITY_GATE
KNOWLEDGE_REUSE
```

### 14.3 Governance Capability Gap

独立于“管理能力”，主要用于跨组织问题：

```text
HORIZONTAL_REPLICATION
CROSS_PRODUCT_GOVERNANCE
COMMON_STANDARD
COMMON_PLATFORM_CAPABILITY
COMMON_TEST_ASSET
COMMON_METRIC
COMMON_CASE_LIBRARY
```

这样能够回答用户提出的：

> 技术能力、管理能力和横向治理能力分别需要补什么。

---

## 15. AI Analysis Model

不建议用一个超大 Prompt 一次生成所有字段。

采用四阶段：

```text
A1 Fact Understanding
       ↓
A2 Cause / Escape Normalization
       ↓
A3 Recurrence Risk Analysis
       ↓
A4 Prevention Capability Gap Analysis
```

---

## 16. A1 — Fact Understanding

输入：

- issue fact
- 原始根因
- 原始解决措施
- 流出分析
- 测试相关字段
- 产品扩展字段

输出：

```text
normalized_problem
problem_summary
phenomenon
failure_object
trigger_condition
impact_summary
```

可复用现有 `AIEnricher` 能力。

---

## 17. A2 — Cause / Escape Normalization

分别执行：

### Occurrence

```text
why_occurred
root_cause
failure_mechanism
contributing_factor
```

### Escape

```text
why_escaped
verification_gap
process_gap
escape_mechanism
```

禁止让模型把 occurrence 与 escape 混为一谈。

---

## 18. A3 — Recurrence Risk Analysis

输入：

```text
Issue Fact
Occurrence Analysis
Escape Analysis
Existing Actions
```

输出：

```text
recurrence_risk_level
recurrence_risk_reason
existing_control_coverage
residual_risk
```

重点问题：

> 当前措施只是修复当前问题，还是已经形成可防止同类问题再发的系统能力？

---

## 19. A4 — Capability Gap Analysis

输入：

```text
Occurrence
Escape
Solution
Recurrence Risk
```

输出：

```text
technical_capability_gaps[]
management_capability_gaps[]
governance_capability_gaps[]
```

每个 Gap 必须包含：

```text
gap_type
gap_category
gap_description
why_needed
related_issue_mechanism
recommended_control
scope
confidence
evidence_refs[]
```

---

## 20. AI Evidence Rule

所有 AI 派生内容必须使用现有 `EvidenceField` 思想。

例如：

```json
{
  "value": "缺少变更影响分析机制",
  "confidence": 0.88,
  "evidence_type": "INFERRED",
  "reason": "问题由变更引入，且字段显示变更影响点未被测试覆盖",
  "source_refs": [
    {
      "source_type": "FIELD",
      "source_id": "PLC:ITR-001",
      "field_path": "变更原因"
    },
    {
      "source_type": "FIELD",
      "source_id": "PLC:ITR-001",
      "field_path": "漏测根因补充说明"
    }
  ]
}
```

禁止无证据直接形成：

```text
管理机制不足
```

---

## 21. SQLite Logical Design

SQLite 是当前持久化实现，不作为上层 Contract。

建议 V1 表：

```text
issue_record
issue_source_raw
issue_product_context
issue_occurrence
issue_escape
issue_solution
issue_verification
issue_product_extension
issue_ai_analysis
issue_capability_gap
issue_evidence_ref
issue_tag
analysis_run
import_batch
```

---

## 22. Main Tables

### 22.1 issue_record

```text
id PK
knowledge_id UNIQUE
business_type
issue_id
title
description
impact
severity
issue_type
is_defect
industry
customer
month
product
platform
department
business_group
created_at
updated_at
```

用于跨 HMI / PLC / IFA 公共查询。

---

### 22.2 issue_source_raw

```text
id PK
knowledge_id FK
source_file
source_sheet
source_row
raw_json
import_batch_id
source_hash
created_at
```

`raw_json` 保存原始行全部字段。

任何统一模型没有覆盖到的产品字段仍然不会丢失。

---

### 22.3 issue_occurrence

```text
knowledge_id FK
original_reason
normalized_reason
root_cause
failure_mechanism
cause_l1
cause_l2
cause_l3
cause_l4
ai_result_json
```

---

### 22.4 issue_escape

```text
knowledge_id FK
is_escape
escape_type
original_reason
normalized_reason
root_cause
escape_l1
escape_l2
escape_l3
escape_l4
verification_gap
ai_result_json
```

---

### 22.5 issue_solution

```text
knowledge_id FK
original_solution
corrective_action
improvement_action
management_action
technical_action
reusable_action
```

---

### 22.6 issue_capability_gap

一条问题可以对应多个 Gap。

```text
id PK
knowledge_id FK
analysis_run_id FK
gap_dimension
gap_category
gap_description
recommended_control
scope
confidence
evidence_json
model_version
prompt_version
created_at
```

`gap_dimension`：

```text
TECHNICAL
MANAGEMENT
GOVERNANCE
```

---

### 22.7 analysis_run

```text
analysis_run_id PK
analysis_type
model_provider
model_name
prompt_name
prompt_version
schema_version
engine_version
started_at
completed_at
status
input_hash
```

保证同一问题能够重新分析，同时保留历史 AI 结果。

---

## 23. Repository Design

现有：

```text
JsonArtifactRepository
```

新增：

```text
IssueKnowledgeRepository        # Contract / Interface
    |
    +-- JsonIssueKnowledgeRepository
    |
    +-- SqliteIssueKnowledgeRepository
```

上层：

```text
KnowledgeService
```

只依赖 `IssueKnowledgeRepository`。

不允许：

```python
sqlite3.connect(...)
```

散落到 Builder / Business / Retrieval 代码中。

---

## 24. KnowledgeService Extension

建议新增能力：

```text
import_issue_records(...)
get_issue(knowledge_id)
find_issue_by_business_id(...)
query_issues(filters)
get_issue_analysis(...)
save_issue_analysis(...)
list_capability_gaps(...)
aggregate_by_cause(...)
aggregate_by_escape(...)
aggregate_by_capability_gap(...)
export_query(...)
```

Repeat Case 原接口保持不变。

---

## 25. Import Pipeline

新增独立 Pipeline：

```text
run-quality-knowledge-import
```

逻辑：

```text
Excel
 ↓
Source Detector
 ↓
HMIAdapter / PLCAdapter / IFAAdapter
 ↓
RawIssueRecord
 ↓
CanonicalIssueBuilder
 ↓
SQLite
```

AI 不在 Import 时强制执行。

原因：

> 数据导入与 AI 分析解耦，避免模型失败导致数据无法入库。

---

## 26. Analysis Pipeline

新增：

```text
run-quality-issue-analysis
```

支持：

```text
--knowledge-id
--business-type
--batch-id
--only-missing
--overwrite
```

流程：

```text
SQLite Fact
 ↓
Analysis Context Builder
 ↓
AI Analyzer
 ↓
Schema Validation
 ↓
Evidence Validation
 ↓
Save New Analysis Run
```

---

## 27. Batch Strategy

必须避免当前 Solution Analysis 中“上下文不断变大”的问题再次发生。

设计要求：

### 单 Case 分析

每个问题独立 Prompt：

```text
1 Issue
+
必要字段
+
必要证据
```

禁止把全部历史问题放入上下文。

### 跨问题洞察

使用两阶段：

```text
SQL聚合 / Retrieval筛选
        ↓
少量候选问题
        ↓
AI总结
```

不能：

```text
全部问题 → 一个Prompt
```

---

## 28. Query Capability

V1 必须至少支持：

```text
按业务查询
按产品查询
按平台查询
按问题分类查询
按发生原因查询
按流出原因查询
按是否漏测查询
按技术能力缺口查询
按管理能力缺口查询
按治理能力缺口查询
按时间查询
```

支持组合条件。

示例：

```text
PLC
AND 是否漏测 = 是
AND escape_l1 = 场景覆盖
AND gap_dimension = MANAGEMENT
```

---

## 29. Statistical Analysis

SQLite 需要直接支持：

```text
TOP发生原因
TOP流出原因
TOP能力缺口
产品 × 发生原因
产品 × 流出原因
平台 × 能力缺口
问题分类 × 防控能力
月份 × 再发风险
横向影响域 × 共性问题
```

这也是选择 SQLite 而非仅保留 JSON Artifact 的主要价值。

---

## 30. Export Design

新增：

```text
ExportService
```

输出：

- CSV
- XLSX

至少两个 Sheet：

```text
Issue_Knowledge
Capability_Gaps
```

可选：

```text
Analysis_Runs
Raw_Mapping
Statistics
```

导出不是数据库 Dump，而是业务可阅读结果。

---

## 31. Compatibility With Repeat Case

冻结原则：

```text
Quality Issue Knowledge
        |
        +----> Retrieval Capability
        |
        +----> Repeat Case
        |
        +----> Quality Risk
        |
        +----> Quality Review
```

本次：

- 不修改 Similarity Analyzer 算法；
- 不修改 Solution Analyzer 判定逻辑；
- 不修改 Repeat Decision；
- 不修改已有 Standard Query Contract；
- 不修改已有 M8.x 输出 Contract。

后续 Repeat Case 如需消费新 Knowledge 字段，通过 Retrieval Profile / Adapter 增量接入。

---

## 32. Existing Standard Case Handling

不建议立即删除或迁移现有 Standard Case。

采用双轨兼容：

```text
Existing Repeat Case
standard_case JSON
       |
       +------ 保持不变

New Quality Knowledge
SQLite
       |
       +------ 新能力
```

在 KnowledgeService 层实现统一访问。

后续成熟后再决定是否把 Standard Case 事实同步到 SQLite。

V1 不做大迁移。

---

## 33. Package Structure Proposal

建议新增：

```text
quality_knowledge/
├── models/
│   ├── issue.py
│   ├── occurrence.py
│   ├── escape.py
│   ├── prevention.py
│   └── analysis.py
├── adapters/
│   ├── hmi_adapter.py
│   ├── plc_adapter.py
│   └── ifa_adapter.py
├── repositories/
│   ├── base.py
│   └── sqlite_repository.py
├── services/
│   ├── import_service.py
│   ├── analysis_service.py
│   ├── query_service.py
│   └── export_service.py
├── analyzers/
│   ├── occurrence_analyzer.py
│   ├── escape_analyzer.py
│   ├── recurrence_analyzer.py
│   └── capability_gap_analyzer.py
├── prompts/
├── schema/
└── migrations/
```

不要把新逻辑继续全部塞进：

```text
builder/
```

否则 Knowledge Capability 与 Repeat Case Pipeline 会再次耦合。

---

## 34. Contract Proposal

建议新增：

```text
QualityIssueDTO
OccurrenceAnalysisDTO
EscapeAnalysisDTO
RecurrenceRiskDTO
CapabilityGapDTO
QualityIssueAnalysisDTO
```

全部继承当前 `VersionedDTO` / `StrictDTO`。

继续复用：

```text
EvidenceField
EvidenceReference
Metadata
```

---

## 35. QualityIssueAnalysisDTO

推荐顶层：

```json
{
  "dto_version": "1.0.0",
  "knowledge_id": "...",
  "occurrence_analysis": {},
  "escape_analysis": {},
  "recurrence_analysis": {},
  "capability_gaps": [],
  "warnings": [],
  "metadata": {}
}
```

AI 输出必须先进行 DTO / JSON Schema 校验，校验成功后才能持久化。

---

## 36. Failure Strategy

AI 分析失败时：

```text
Raw Data      保留
Fact Data     保留
Previous AI   保留
New Run       FAILED
```

不得：

- 清空旧结果；
- 写入半结构化数据覆盖旧结果；
- 导致整个批次回滚。

单问题失败不影响其他问题。

---

## 37. Idempotency

导入唯一识别建议：

```text
business_type
+
issue_id
+
source_hash
```

相同数据重复导入：

```text
SKIP
```

内容变化：

```text
NEW SOURCE VERSION
```

而不是覆盖原始记录。

---

## 38. Traceability

完整追溯：

```text
Capability Gap
      ↓
Analysis Run
      ↓
AI Evidence
      ↓
Canonical Issue Field
      ↓
Raw Field
      ↓
Excel / Sheet / Row
```

这是本设计必须实现的核心能力。

---

## 39. Security / Data Minimization

由于 IFA 包含客户信息：

AI Prompt Builder 必须支持：

```text
include_customer_name = false
include_sensitive_notes = false
```

默认仅向模型发送分析所必需字段。

原始 SQLite 可以保留客户字段，但 AI 上下文可脱敏。

---

## 40. V1 Delivery Scope

### M1 — Foundation

交付：

- QualityIssue DTO
- 三业务 Adapter
- SQLite schema
- SQLite Repository
- Import Batch
- Raw preservation
- 基础查询

### M2 — AI Analysis

交付：

- Occurrence Analyzer
- Escape Analyzer
- Recurrence Analyzer
- Capability Gap Analyzer
- Evidence / Schema validation
- Analysis Run versioning

### M3 — Query & Export

交付：

- QueryService
- Cross-business filters
- Aggregation
- CSV/XLSX export

### M4 — Existing Capability Integration

交付：

- KnowledgeService extension
- Retrieval adapter
- Repeat Case compatibility regression

---

## 41. Acceptance Criteria

### Data

- HMI / PLC / IFA 均可导入；
- 原始字段 100% 保留；
- 公共字段正确统一；
- 产品差异字段不丢失；
- 三种分类维度不混淆。

### AI

- 能分别回答 Why Occurred / Why Escaped；
- 能识别 Technical / Management / Governance Gap；
- 每个 Gap 有 evidence；
- AI 结果不覆盖 Fact；
- 模型、Prompt、Schema、Run 均可追溯。

### Storage

- SQLite 可独立查询；
- 单 Case AI 失败不影响数据；
- 支持版本化分析结果；
- 上层不直接依赖 SQLite。

### Query

可以回答：

1. 哪类问题最常发生？
2. 哪类问题最容易漏测？
3. 哪些问题反复发生的根因相似？
4. 哪些改进仍停留在单点修复？
5. 当前最缺的技术防控能力是什么？
6. 当前最缺的管理机制是什么？
7. 哪些问题需要跨产品横向治理？
8. 哪些能力缺口同时出现在 HMI / PLC / IFA？

### Export

- 支持 Excel；
- 支持 CSV；
- 导出内容可直接用于人工分析。

---

## 42. Design Decision Summary

本设计最终锁定 8 个关键决策：

1. **现有 Repeat Case 不重构。**
2. **本次是 Knowledge Capability Extension。**
3. **Raw / Fact / AI Derived 三层继续严格分离。**
4. **Where / Why Occurred / Why Escaped 三套分类独立。**
5. **公共模型 + Product Extension，而不是三业务强制同构。**
6. **SQLite 用于结构化查询，但不成为上层 Contract。**
7. **Prevention Capability Gap 是新增核心知识对象。**
8. **最终目标从“知道问题为什么发生”升级为“知道怎样防止同类问题再次发生”。**

---

## 43. Engine Implementation Input

Engine 窗口不需要重新讨论需求与架构。

其实现输入为：

```text
Requirements:
KNOWLEDGE_QUALITY_ISSUE_ANALYSIS_REQUIREMENTS_V1.0

Design:
KNOWLEDGE_QUALITY_ISSUE_ANALYSIS_DESIGN_V1.0

Baseline Code:
REPEAT_CASE_ENGINE_V2.4_M6_SOLUTION_OPTIMIZED
```

Engine 第一阶段应直接实施：

```text
M1 Foundation
```

即：

```text
DTO
→ Product Adapter
→ SQLite Schema
→ Repository
→ Import Pipeline
→ Query Smoke Test
```

第一阶段不接 AI，先证明三类业务数据可以被无损、正确地结构化进入 Knowledge。
