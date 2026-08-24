# Development Audit Ledger

更新时间：2026-08-24  
作用：保存已经确认的架构事实、开发决策和审计证据。后续开发必须先读取本文件，只对代码变化或新增故障做增量审计，不重复进行全工程审计。

## 1. 当前冻结决策

- `P0/P1` 新架构是唯一后续开发基线。
- 不迁移、不读取、不兼容旧业务数据库。
- 使用全新数据库和真实数据完成验收。
- PLC 字段作为当前标准字段初始基线；后续字段类型允许扩展。
- `source_id` 不是必填业务字段，仅是部分飞书导出数据自带字段。
- 核心业务闭环为：产品配置 → Mapping → Excel Preview → 差异处理 → Draft → Validate / Activate → Import → AI 分析 → 人工确认 → 质量洞察 → 正向风险评估。
- UED 延续原系统的导航、布局、中文表达和操作习惯，但不恢复旧代码架构。
- AI 分析需要支持可配置问题级并发；默认并发数为 2。

以上决策只有在真实 E2E 证据证明不可行，或产品需求明确变更时才重新评审。

## 2. 已确认架构事实

### 2.1 启动与数据库

- 原业务入口 `knowledge-web` 使用 `knowledge/quality_issue_v1.db`。
- 新入口 `knowledge-p1-start` 使用 `knowledge/quality_capability_p1.db`。
- P1 初始化不会迁移原数据库中的问题、AI、人工分析或 Mapping 数据。
- 新旧数据库契约不同，不能直接互换数据库文件。
- 因已冻结“不考虑新旧数据兼容”，后续不再为此设计迁移器或兼容适配层。

### 2.2 已发现的当前风险

- 新入口曾导致用户感知原 AI 数据和原业务流程消失；本质是入口和数据库切换，不是已证明的数据删除。
- 新 Mapping 与原 Mapping 的交互、字段目录和业务闭环存在体验及功能落差，需要以真实 Excel 验证并修复。
- 新页面 UED 与原系统风格存在明显差异，需要按原 UED 要求统一。
- 不能仅凭单元测试通过判定业务可用，必须完成真实数据 E2E。

## 3. 可继续复用的成果

- P0/P1 分类体系、Prompt、固定输出 Schema 和评分设计。
- 工程质量能力与质量管理能力双维度分析。
- MRC、生命周期、证据、置信度和人工待确认契约。
- 问题级并发分析服务与相关测试思路。
- 正向风险案例和评估的数据模型、服务及页面成果。
- 新工作台、详情、洞察页面中符合原 UED 要求的局部组件。

## 4. 禁止重复审计规则

下列内容在没有新证据时不得重复审计：

- 是否兼容或迁移旧数据库。
- 是否以 P0/P1 为开发基线。
- PLC 是否作为当前字段基线。
- `source_id` 是否必填。
- 是否需要恢复旧代码作为主系统。
- 是否需要真实 Excel E2E。

审计结论失效只允许以下触发条件：

1. 相关文件在结论记录后发生实质修改；
2. 真实 E2E 出现与结论矛盾的证据；
3. 产品需求明确改变；
4. 数据库或接口契约版本升级。

## 5. 后续工作方式

每个开发阶段采用以下固定循环：

1. 读取本审计台账和最近变更记录；
2. 运行一个明确的真实场景；
3. 在首个失败点停止场景；
4. 只审计失败点及直接上下游；
5. 修复并增加专项测试；
6. 继续同一场景；
7. 阶段结束后只运行一次相关回归；
8. 发布前运行一次全量回归并更新本台账。

不得以“可能还有问题”为理由无限扩大审计范围。

## 6. 当前下一步

从全新 P1 数据库执行真实数据最小闭环，第一阶段只处理：

```text
产品配置 → Mapping → Excel 表头识别 → Preview → Draft → Validate / Activate → Import
```

该链路通过前，不扩展到 AI、人工确认和洞察页面；链路通过后再进入下一阶段。

## 7. 增量记录模板

后续每次审计或关键修复只追加一条：

```text
日期：
范围：
触发原因：
复现证据：
确认结论：
修改文件：
专项测试：
是否影响既有结论：否 / 是（说明章节）
下一步：
```

## 8. 增量记录

### 2026-08-24：数据接入与 Mapping 差异闭环

- 范围：P1 新数据库的产品 Mapping、Excel Preview、差异处理、Draft、Validate、Activate。
- 触发原因：页面只能展示未匹配/冲突，不能直接选择已有字段或生成 Draft；新产品没有 ACTIVE Mapping 时无法先上传比较。
- 复现证据：真实 Excel `/Users/xiamin/Downloads/表格.xlsx` 在 PLC Preview 中识别 17 个表头，其中 16 个未匹配，“编号”导致必填问题编号缺失。
- 确认结论：允许使用 Starter Draft 做首次 Preview，但 Draft Preview 禁止正式导入；差异行支持“映射已有字段 / 仅保留 Raw”，提交只更新 Draft；一个源表头新映射到多个目标时阻止激活。
- 修改文件：`quality_knowledge/p0/intake_service.py`、`quality_knowledge/standard_fields/repository.py`、`quality_knowledge/web/api_v2.py`、`quality_knowledge/web/templates/p0_console.html`、`quality_knowledge/web/static/p0_console.js`、`quality_knowledge/web/static/p0_console.css`、`tests/test_quality_capability_p0_api.py`。
- 专项测试：关联回归 32 passed；JavaScript 语法检查通过；真实页面完成 Preview → 差异选择 → PLC V2 Draft → Validate → Activate → 重新 Preview，`编号` 成功映射到 `ISSUE_FACT.business_issue_id`；浏览器错误日志为空。
- 是否影响既有结论：否。
- 下一步：使用真实质量问题 Excel 完成正式 Import；通过后进入 AI 分析与并发结果持久化阶段。

### 2026-08-24：PLC YAML 全字段样例与正式 Import 闭环

- 范围：以 `quality_knowledge/config/plc_fields.yaml` 为唯一字段来源，构建 P1 全字段验收数据并执行正式导入。
- 触发原因：避免手工猜测表头和字段，只围绕当前已冻结的 PLC 字段基线验证新架构。
- 复现证据：生成单 Sheet、59 个业务字段、5 条典型质量问题的 `outputs/quality_capability_p1/plc_quality_issue_full_fields_e2e.xlsx`；不包含非必填的 `source_id`。
- 确认结论：全新 P1 数据库完成 Preview → Confirm Import；Preview 识别 59 个表头、5 行数据、无冲突、无必填缺失、允许导入；Import 结果为 5/5 成功、0 失败、0 重复，数据库保存 5 个问题和 5 个当前版本。
- 样例覆盖：软件变更与通信、固件/模块版本兼容、eMMC/掉电可靠性、硬件批次与温度、已知修复未合入发布分支；包含发生/流出 L1-L4、解决措施、验证、再发和扩展字段。
- 验证：工作簿范围 `A1:BG6`；公式错误扫描为 0；渲染预览已人工检查。
- 是否影响既有结论：否。
- 下一步：使用这 5 条已导入问题验证默认并发 2 的四阶段 AI 分析、结果持久化和工作台/详情读取。

### 2026-08-24：并发 AI 分析状态与持久化闭环

- 范围：P1 问题工作台批量分析、模型运行状态、四阶段结果持久化和详情读取。
- 触发原因：模型配置未真正就绪时仍创建分析器；单个问题四阶段全部失败时，批量汇总仍可能显示为“成功”。
- 复现证据：当前 `config/model.yaml` 已启用模型，但所需环境变量未设置；修复前只能在实际提交后看到阶段失败，且批量成功数口径不准确。
- 确认结论：启动时先验证模型配置；工作台显示“AI 服务已就绪/未就绪”并在未就绪时禁用批量提交。批量结果明确区分 `SUCCEEDED`、`PARTIAL`、`FAILED`，四阶段全失败不再计入成功。
- 修改文件：`quality_knowledge/services/v2_analysis_service.py`、`quality_knowledge/services/v2_batch_analysis_service.py`、`quality_knowledge/web/p0_app.py`、`quality_knowledge/web/api_v2.py`、`quality_knowledge/web/templates/p0_issues.html`、`quality_knowledge/web/static/p0_issues.js`、`tests/test_p1_batch_analysis.py`。
- 专项测试：分析、批量、工作台和 API 相关回归 27 passed；JavaScript 语法检查通过。
- E2E 证据：对上一阶段正式导入的 5 条 PLC 样例以并发 2 完成确定性四阶段分析，5 成功、0 部分、0 失败；保存 20 条阶段运行、10 条 MRC、5 条能力缺口、80 条证据、5 条待确认事项；详情 API 可读每个问题的完整分析集。
- 是否影响既有结论：否。
- 下一步：验证人工确认独立修订、质量洞察聚合和精确下钻。

### 2026-08-24：人工确认、洞察与正向风险闭环

- 范围：人工确认独立修订、质量洞察聚合/下钻、历史案例发布和正向风险评估。
- 复现证据：在 5 条 PLC E2E 问题中修正一条发生 MRC；随后按 PLC 范围读取双维度洞察，并将一条分析完成的问题发布为风险案例后评估新设计材料。
- 确认结论：人工修订保存为 Revision 1，AI 原始 MRC 保持不变，当前有效 MRC 正确切换为人工确认值；洞察覆盖 5/5 已完成问题，分类覆盖率 100%，工程矛盾可精确下钻到 5 条关联问题；风险案例发布成功，新设计材料匹配 1 条风险且控制覆盖判定为 `COVERED`。
- E2E 结果：人工确认 HTTP 201；洞察 HTTP 200；MRC×能力矩阵 3 个单元，生命周期×能力矩阵 1 个单元；精确下钻 HTTP 200、total=5、返回 5 条；风险案例发布 HTTP 201；正向评估 HTTP 201。
- 是否影响既有结论：否。
- 下一步：执行 P0/P1 相关全量回归并形成可交付包。
