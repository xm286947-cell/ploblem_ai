# 质量问题分析引擎：账号迁移与续开发交接说明

更新时间：2026-08-23  
交接基线：`ACCOUNT_HANDOFF_RC1`  
项目名称：Knowledge Quality Issue Analysis Engine V1.0

## 1. 迁移结论

新账号续开发应直接使用本交接包中的完整源码快照，不要重新从早期 P2、P3 RC1 或单个补丁开始合并。

虽然源码目录仍保留历史名称 `P2_DATA_INTAKE_MAPPING_UED_RC1`，其中代码已经持续合入 P3、产品配置、Prompt V2、公共字段目录、动态业务以及月份导航等后续改动。目录名称不是当前能力版本号。

最近一次旧完整包是：

```text
KNOWLEDGE_QUALITY_ISSUE_ANALYSIS_ENGINE_V1.0_P3_RELEASE_RC8_SHARED_CATALOG_FULL.zip
```

RC8 之后还有以下变更：

- 问题工作台业务筛选动态读取产品配置；
- 批量 AI 分析业务筛选动态读取产品配置；
- 问题工作台增加月份列和月份筛选；
- 问题详情增加上一条、下一条和当前位置。

因此本交接包的源码快照高于 RC8，应作为唯一续开发基线。

## 2. 产品定位

本产品用于把来自市场、研发和质量体系的原始质量问题数据转化为可追溯的 Knowledge，并完成单问题分析、人工确认、共性能力缺口识别和质量洞察。

当前核心链路：

```text
Excel 原始问题
  → 表头识别
  → Mapping Preview
  → 差异处理与 Mapping Draft
  → Validate / Activate
  → Preview-before-Commit
  → Knowledge Current Issue + Version
  → 四阶段 AI 分析
  → 人工分析与待确认事项
  → 质量洞察 / 能力缺口 / 导出
```

设计原则：

- 原始数据必须保留，标准化不能覆盖 Raw；
- Current Issue 与 Issue Version 分离，历史版本可追溯；
- Preview 与正式 Import 分离；
- Mapping 使用 Draft → Validate → Activate；
- AI 推理结果与人工确认结果分开保存；
- 产品背景和问题领域分开建模；
- 新产品尽量复用公共标准字段，避免各业务自行创造不统一的数据结构。

## 3. 当前已实现能力

### 3.1 数据与 Knowledge

- SQLite 持久化；默认数据库为 `knowledge/quality_issue_v1.db`。
- 业务唯一键：`business_type + business_issue_id`。
- 支持 NEW、UPDATED + New Version、SKIPPED、FAILED。
- Original / Normalized / Derived 分层。
- Current Issue、Issue Version、Raw Source、Import Batch、Import Error 可追溯。
- 人工分析、AI 分析运行、调试信息、能力缺口、待确认事项均持久化。

### 3.2 数据接入与表头识别

- 支持 `.xlsx`、`.xlsm`。
- 支持 HMI、PLC、IFA，并支持通过产品配置扩展 ROBOT、VISION 等新产品。
- 修复过公式缓存导致 51 列工作表被识别为 1 列的问题。
- 修复过 openpyxl 错误工作表 dimension 导致只读取 `A1:A1` 的问题。
- 增加数据接入诊断日志：上传、Workbook 打开、Sheet 候选、表头行、列宽、数据行、最终 Preview。
- 提供独立表头读取/诊断能力和真实表头检查思路。

### 3.3 Mapping

- P1 Preview-before-Commit 已闭环。
- Preview 绑定当时使用的 ACTIVE Mapping ID/Version。
- ACTIVE Mapping 在 Preview 后切换时，Confirm 会拒绝并要求重新预检。
- 冲突和必填缺失阻断正式导入。
- Confirm 幂等，不重复写入。
- P2 数据接入工作台和字段映射工作台已完成。
- Mapping 支持 Draft、Validate、Activate、版本历史和 YAML 来源追溯。
- 新产品可创建独立 Mapping Draft。
- 新产品默认从 HMI / PLC / IFA ACTIVE Mapping 与随包 YAML 汇总公共字段目录。
- 公共字段按 `target_domain + target_field` 去重。
- 只继承标准字段和 Alias 建议，不复制其他产品 Excel 源字段。
- 已存在的新产品 Draft 可重复补齐缺失公共字段，并保留用户修改。
- 字段选择器显示中文业务名、Canonical Key、Target、Alias，并支持检索。
- 已修复 `ALIAS_TARGET_NOT_FOUND` 的兼容处理和可读错误提示。
- 已修复基于 DRAFT Preview 生成下一版 Draft 时误报 `MAPPING_CHANGED_AFTER_PREVIEW`。

### 3.4 历史迁移与发布

- 提供 `knowledge-release-migrate`：Dry Run → Migration Report → Apply。
- 迁移覆盖 Knowledge、Mapping、配置、人工分析等历史数据保护与审计。
- 提供 HMI / PLC / IFA E2E 验收框架。
- 迁移和导入均保持版本、来源和历史记录可追溯。

### 3.5 产品配置与问题领域

- 系统设置中提供产品配置入口。
- 默认产品：HMI、PLC、IFA。
- 支持新增机器人、视觉、硬件、机械等产品。
- 产品字段包括编码、名称、产品类型、默认问题领域、启用状态、排序。
- 问题增加主领域：AUTO、SOFTWARE、HARDWARE、MECHANICAL、EMBEDDED。
- 同一个产品的不同问题可拥有不同领域。
- 支持导入预设、Excel 行级领域、产品默认值和 AI 判断。
- 支持问题领域批量设置及分析触发。
- 产品配置、问题工作台、批量 AI 分析和字段 Mapping 已完成动态业务联动。

### 3.6 AI 分析与人工确认

- 单问题四阶段分析：Occurrence、Escape、Recurrence、Capability Gap。
- 能力缺口维度：Technical、Management、Governance。
- Prompt V2 支持产品领域和问题主题预选。
- 问题主题包括功能、性能、可靠性、兼容性、安全、工艺/流程、装配、需求、版本组合、交付等。
- 生命周期阶段可预选或自动判断。
- 分析兼顾软件、嵌入式、硬件和机械，不再只按纯软件研发视角。
- 分析关注质量专业、系统工程、生命周期、解决方案组合兼容、客户影响和客户体验。
- AI 可以输出待人工确认事项；人工分析页面可确认、修正、标记未解决或不适用。
- 人工分析保留原始问题、标准化数据、AI 摘要和确认记录。

### 3.7 问题工作台与详情页

- 问题工作台支持搜索、业务、产品、平台、严重度、再发风险、AI 状态、人工状态和人工字段筛选。
- 业务下拉动态读取启用产品，不再固定 HMI / PLC / IFA。
- 工作台显示月份，并可按数据库中的实际月份值筛选。
- 详情页显示月份。
- 详情页支持上一条、下一条和当前位置，首尾按钮自动禁用。
- 当前上一条/下一条按工作台默认顺序浏览，不保留复杂组合筛选上下文。

### 3.8 质量洞察

- 发生原因、流出原因、产品分布、再发风险、能力缺口和共性能力缺口展示。
- 已修复 TOP 原因长期显示空或未分类的问题呈现。
- 能力缺口详情由原始 JSON 改为业务页面。
- 共性能力缺口“查看相关问题”按相关问题集合过滤，不再直接进入全部问题清单。
- 治理优先级相关问题链接已打通。

## 4. 关键业务模型

### 4.1 产品与问题领域

产品是问题发生的载体；问题领域决定分析视角。

示例：

- PLC 通讯逻辑错误：SOFTWARE 或 EMBEDDED；
- PLC 主板损坏：HARDWARE；
- 机器人结构异响：MECHANICAL；
- IFA 与 PLC/HMI 版本组合不兼容：SOFTWARE / 版本组合与解决方案兼容问题。

当前刻意保持单一主问题领域，不做多领域权重和复杂产品关系图。

### 4.2 Mapping 公共字段目录

HMI、PLC、IFA 已适配字段构成公共标准字段来源。新产品不应让每个用户重新创造字段，而应先复用公共字段，只对确实新增的业务概念创建扩展字段。

```text
HMI / PLC / IFA ACTIVE Mapping + 标准 YAML
  → 公共字段目录
  → 新产品 Mapping Draft
  → 上传真实 Excel 比较差异
  → 选择已有字段 / Raw / 扩展字段 / 不启用
  → Validate
  → Activate
```

### 4.3 Prompt 输出约束

Prompt 可以调整分析角度，但存入数据库的输出结构必须相对固定。不要让业务用户直接改变 JSON Schema；应使用可配置分析 Profile、Prompt Version 和受控枚举。

四阶段输出和人工确认必须继续保持：

- 固定 schema/version；
- evidence 与 confidence；
- unknown / open question；
- AI 结论与人工修正可区分；
- 历史运行可追溯。

## 5. 代码结构

主要开发区域：

```text
main.py                                  CLI 入口
quality_knowledge/
  adapters/                              Excel 业务适配
  config/                                HMI/PLC/IFA YAML 与导入配置
  mapping/                               Mapping 模型、仓库、运行时、服务、迁移
  migration/                             迁移模块
  models/                                问题与分析模型
  repositories/                          SQLite 数据访问
  services/                              导入、查询、分析、导出、接入会话
  prompts/                               四阶段 Prompt
  human_analysis/                        人工分析字段和记录
  web/app.py                             FastAPI 路由与页面组装
  web/templates/                         Jinja 页面
  web/static/                            UI 样式与脚本
tests/                                   回归和专项测试
```

旧 Repeat Case Engine 的模块仍在工程中，不能在没有完整回归的情况下大规模删除或重构：

```text
analysis/ builder/ common/ compatibility/ parser/ parsing/
presentation/ repositories/ retriever/ services/ schema/
```

## 6. 启动与常用命令

建议环境：Python 3.10+，Windows / Linux / macOS。

```powershell
pip install -r requirements.txt
python main.py --help
python main.py knowledge-web --host 127.0.0.1 --port 8080
```

数据库默认路径：

```text
knowledge/quality_issue_v1.db
```

AI 配置：

```text
config/model.yaml
```

当前示例配置读取环境变量 `acca`。迁移账号时不要把真实 API Key 写入 YAML 或交接文档，应在新环境单独设置环境变量。

AI 检查：

```powershell
python main.py knowledge-ai-check
python main.py knowledge-ai-check --live
```

Mapping 首次初始化：

```powershell
python main.py knowledge-mapping-migrate --all --apply
```

如果数据库中已存在部分 Mapping，初始化必须保持幂等。过去出现过 `mapping_alias` UNIQUE 冲突，后续改动不能重新引入重复 Alias 插入。

历史数据升级：

```powershell
python main.py knowledge-release-migrate --db .\knowledge\quality_issue_v1.db --report .\output\migration_report.json
python main.py knowledge-release-migrate --db .\knowledge\quality_issue_v1.db --report .\output\migration_report.json --apply
```

先 Dry Run 并审查 Report，再 Apply。不要直接覆盖生产数据库；执行前备份 SQLite 文件。

## 7. 数据与账号迁移注意事项

Codex/ChatGPT 账号迁移不会自动迁移本地数据库、输入 Excel、输出报告或密钥。

建议分别迁移：

1. 本交接源码包；
2. 真实 SQLite 数据库的独立备份；
3. 必要的 Mapping YAML 和模型配置；
4. 不含密钥的环境说明；
5. 经脱敏的真实 HMI / PLC / IFA / ROBOT E2E 样本；
6. 历史 Migration Report 和 Release Acceptance 结果。

不要把以下内容上传到不受控账号：

- 客户名称、市场问题原文和未脱敏 Excel；
- 真实 API Key；
- 带客户数据的 SQLite；
- 诊断日志中的文件名、字段和值；
- 原始 AI 请求和响应日志。

本交接源码快照不包含运行时数据库、数据接入日志或真实业务输入文件。

## 8. 验证状态

历史已知回归记录：

- P2 专项 + P1/A4/A5：13 passed；
- P2 当时完整工程回归：232 passed，0 failed；
- P3 迁移与 HMI/PLC/IFA E2E 框架已建立。

RC7、RC8 之后的部分小补丁在当前开发机只完成了 Python 语法、静态契约和专项数据层检查，因为当前本机缺少完整 pytest/FastAPI/Pydantic 依赖。新账号接手后的第一件事应是安装依赖并跑全量测试：

```powershell
python -m pytest -q
```

注意：当前 `requirements.txt` 中存在 `httpx2>=0.29,<1`。如果标准 PyPI 环境安装失败，应核实它是否为内部包名；若不是，可能需要更正为项目实际使用的 HTTP 客户端包。不要在未确认前静默修改依赖。

## 9. 已知边界与待开发事项

以下内容尚未完整实现，不要在新账号中误标为已完成：

1. 所有问题或单产品的整体质量报告：核心矛盾、主要原因链、客户影响、共性风险和治理优先级。
2. 类 Grafana 的用户自配置度量看板。
3. 低质量原始市场数据作为补充数据源，与三业务标准化分析数据互补。
4. SQLite 改为飞书数据库；当前已明确暂缓。
5. 复杂产品组成、产品依赖图、多领域问题权重；当前 MVP 不做。
6. 详情页上一条/下一条保留工作台所有复合筛选上下文；当前按默认全局顺序。
7. 最近补丁合并后的完整 Release Acceptance，需要在正式依赖环境和真实脱敏样本上重新执行。

推荐下一阶段优先级：

```text
P0  全量回归 + 真实数据 Release Acceptance
P1  产品级整体质量报告
P2  数据质量评分与低质量原始数据接入
P3  可配置质量度量看板
```

## 10. 新账号首次接手检查清单

- 解压本交接完整包，不从旧 RC 叠加补丁。
- 阅读本文件和 `README.md`。
- 安装依赖并处理依赖名称问题。
- 运行 `python main.py --help`。
- 运行全量 pytest。
- 使用空数据库启动 Web。
- 执行 Mapping 初始化并验证幂等。
- 新建 ROBOT/VISION 产品，确认产品配置、Mapping、问题工作台和批量 AI 联动。
- 用脱敏 HMI / PLC / IFA / ROBOT Excel 验证表头、Preview、Draft、Activate、Confirm。
- 备份并 Dry Run 真实历史数据库迁移。
- 检查 AI 配置和环境变量，不复制旧账号密钥。
- 完成后生成新的 IMPLEMENTATION_STATUS、MIGRATION_RESULT、E2E_RESULT 和 TEST_RESULT。

## 11. 可直接发给新账号的启动提示词

```text
你将继续开发“Knowledge Quality Issue Analysis Engine V1.0”。

请先完整阅读 docs/ACCOUNT_MIGRATION_HANDOFF.md、README.md、现有 Release Manifest 和 tests，随后检查代码，不要根据聊天摘要猜测实现。

当前唯一开发基线是 ACCOUNT_HANDOFF_RC1 完整源码快照。它已经合并 P1 Preview-before-Commit、P2 Data Intake/Mapping UED、P3 迁移与 E2E、产品配置与问题领域、Prompt V2 与人工确认、新产品公共 Mapping 字段目录、字段选择与检索、动态业务筛选、月份筛选以及详情页上一条/下一条。不要再叠加旧 RC1-RC8 或历史补丁。

必须保持以下边界：
1. 不重构旧 A1-A7 和 Repeat Case 主架构；
2. 保护 Original/Normalized/Derived、Current Issue/Version 和历史数据；
3. Mapping 继续使用 Draft → Validate → Activate；
4. Import 继续使用 Preview-before-Commit；
5. AI 输出格式保持版本化和结构稳定，未知信息进入待人工确认；
6. 产品与问题领域分离，新产品优先复用公共字段；
7. 所有数据库迁移必须 Dry Run → Report → Apply；
8. 修改后必须运行专项测试和全量回归，并如实报告无法执行的测试。

接手第一步不是开发新功能，而是：安装依赖、运行全量测试、使用空库和历史库各做一次启动/迁移验证、使用脱敏 HMI/PLC/IFA/ROBOT 样本做 E2E，然后输出基线审计报告。
```

## 12. 交付口径

后续每次交付至少包含：

```text
IMPLEMENTATION_STATUS
MIGRATION_RESULT（涉及迁移时）
E2E_RESULT
MODIFIED_FILES
DATABASE/CONTRACT_CHANGES
TEST_RESULT
COMPATIBILITY_RESULT
RELEASE_PACKAGE
```

完整包应包含全部当前源码；增量补丁只能包含相对上一明确基线发生变化的文件，并附 Manifest、覆盖方式和校验值。
