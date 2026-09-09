# KNOWLEDGE QUALITY ISSUE ANALYSIS ENGINE

**Package:** `KNOWLEDGE_QUALITY_ISSUE_ANALYSIS_ENGINE_V1.0_M5_RC_CONFIG1`  
**Status:** Release Candidate + Field Configuration Initial Baseline  
**Design Baseline:** `KNOWLEDGE_QUALITY_ISSUE_ANALYSIS_DESIGN_V1.0` (Frozen)  
**Existing Capability Baseline:** `REPEAT_CASE_ENGINE_V2.4_M6_SOLUTION_OPTIMIZED`
**Latest cumulative patch:** `V1.1_P2_RC2_PATCH53_20260909`（场景库与行业场景看板按产品配置分组）

## 1. 用途

用于将 HMI / PLC / IFA 历史质量问题无损导入 Knowledge DB，并形成：

- Original / Normalized / Derived 三层数据；
- Current Issue + Issue Version；
- Why Occurred / Why Escaped / Where 三类独立知识；
- AI 再发风险分析；
- Technical / Management / Governance Capability Gap；
- Web / CLI 查询；
- Statistics；
- XLSX / CSV 导出。

完整链路：

```text
HMI / PLC / IFA Excel
        ↓
Import / Adapter
        ↓
Original Raw
        ↓
Normalized Issue + Issue Version
        ↓
SQLite
        ↓
Web / CLI
        ↓
AI Analysis
        ↓
Occurrence / Escape / Recurrence
        ↓
Technical / Management / Governance Gap
        ↓
Statistics
        ↓
XLSX / CSV
```

## 2. 当前里程碑

| Milestone | Scope | Status |
|---|---|---|
| M1 | Data Foundation | Completed |
| M2 | Web Foundation | Completed |
| M3 | AI Analysis | Completed |
| M4 | Statistics / Export | Completed |
| M5 | Compatibility Regression | Completed |
| CONFIG1 | HMI / PLC / IFA 字段配置初版 | Included |

> 当前仍为 RC。正式 RELEASE 前应使用真实 HMI / PLC / IFA Excel 完成 E2E Release Acceptance。

## 3. 目录

```text
.
├── main.py
├── requirements.txt
├── quality_knowledge/
│   ├── adapters/
│   ├── config/
│   │   ├── import.yaml
│   │   ├── hmi_fields.yaml
│   │   ├── plc_fields.yaml
│   │   ├── ifa_fields.yaml
│   │   └── README_CONFIG.md
│   ├── models/
│   ├── repositories/
│   ├── services/
│   ├── prompts/
│   └── web/
├── tests/
└── README.md
```

## 4. 字段配置说明

`quality_knowledge/config/` 是本包新增的人工维护配置基线。

- `import.yaml`：Header 扫描、标准化、导入行为。
- `plc_fields.yaml`：PLC 字段别名与语义映射；已结合真实 PLC 表头增强。
- `hmi_fields.yaml`：HMI 初版字段配置。
- `ifa_fields.yaml`：IFA 初版字段配置。

配置目标是：

```text
稳定机制 → Python Engine
业务字段 / 别名 → YAML
```

典型场景：

```text
ITR单号
ITR 单号
ITR编号
ITR号
```

都应归一到同一业务字段。

PLC 初版同时考虑：

```text
问题原因定位（×开发填写×）
问题解决方案（×开发填写×）
是否共性问题（×开发填写×）
横向影响域
纵向影响域
是否已有用例
是否为必测项
是否已自动化
是否场景问题
```

### 重要状态说明

本包把 YAML 配置与 M5 RC Engine **统一打包**，作为 CONFIG1 配置基线；原 M5 RC 中已有 Adapter 代码仍保留。  
因此，修改 YAML 后是否立即影响运行，取决于后续 `Configurable Adapter / Header Normalizer` 接入状态。正式 RC1 应完成“配置驱动 Adapter”，避免再修改 `products.py` 维护业务别名。

Raw Header / Raw Row 的原则不变：**原文必须保留；Normalized Header 只用于匹配。**

## 5. 环境

建议：

```text
Python 3.10+
Windows / Linux / macOS
SQLite
```

安装：

```powershell
pip install -r requirements.txt
```

检查：

```powershell
python main.py --help
```

## 6. 推荐输入目录

```text
input/
├── HMI/
├── PLC/
└── IFA/

output/
knowledge/
logs/
```

## 7. 导入

自动识别业务：

```powershell
python main.py knowledge-import --input ".\input\PLC\PLC问题清单.xlsx"
```

也可以显式指定：

```powershell
python main.py knowledge-import `
  --input ".\input\PLC\PLC问题清单.xlsx" `
  --business-type PLC
```

指定数据库：

```powershell
python main.py knowledge-import `
  --input ".\input\PLC\PLC问题清单.xlsx" `
  --db ".\knowledge\quality_issue_v1.db"
```

### 导入结果

主要关注：

```text
total
new
updated
skipped
failed
status
diagnostics
```

规则：

```text
第一次出现           → NEW
业务键相同且内容相同 → SKIPPED
业务键相同但内容变化 → UPDATED + New Issue Version
单行异常             → FAILED / import_error，其他行继续
```

业务唯一键：

```text
business_type + business_issue_id
```

## 8. HEADER_NOT_DETECTED

如果报：

```text
HEADER_NOT_DETECTED
```

先确认：

1. 表头真实所在行；
2. ITR/TRC 字段实际名称；
3. 是否存在空格、换行、全角空格；
4. 是否包含类似 `（×开发填写×）` 的字段说明；
5. Sheet 是否正确。

当前真实 PLC 样本已经发现：

```text
代码旧别名：ITR单号
真实表头：ITR 单号
```

因此本包已把相关别名写入 `plc_fields.yaml`。

如果需要人工补字段，优先修改 YAML 配置，不建议继续扩散 Python 硬编码。

## 9. 查询

```powershell
python main.py knowledge-query --business-type PLC
```

按业务编号：

```powershell
python main.py knowledge-query `
  --business-type PLC `
  --business-issue-id ITR20260323051CS
```

默认查询 Current Version。

## 10. Web

启动：

```powershell
python main.py knowledge-web `
  --db ".\knowledge\quality_issue_v1.db" `
  --host 127.0.0.1 `
  --port 8080
```

浏览器：

```text
http://127.0.0.1:8080
```

Web 入口：

1. Import
2. Issues
3. Issue Detail
4. AI Analysis
5. Statistics
6. Export

## 11. AI 分析

单问题：

```powershell
python main.py knowledge-analyze `
  --knowledge-id <KNOWLEDGE_ID>
```

批量：

```powershell
python main.py knowledge-analyze `
  --business-type PLC `
  --only-missing
```

每个问题独立分析：

```text
Problem Understanding
      ↓
Occurrence
      ↓
Escape
      ↓
Recurrence
      ↓
Capability Gap
```

禁止把全部历史问题持续叠加进一个 Prompt。

AI 失败时：

```text
Raw             保留
Normalized      保留
Previous AI     保留
New Run         FAILED
```

可 Retry。

## 12. Statistics

```powershell
python main.py knowledge-stats --business-type PLC
```

支持：

- TOP 发生原因；
- TOP 流出原因；
- 产品/业务分布；
- TOP Technical Gap；
- TOP Management Gap；
- TOP Governance Gap；
- 跨产品共性 Capability Gap。

统计默认基于：

```text
Current Issue Version
+
Latest Valid COMPLETED Analysis
```

## 13. Export

Excel：

```powershell
python main.py knowledge-export `
  --output ".\output\quality_knowledge.xlsx" `
  --format xlsx
```

CSV Issues：

```powershell
python main.py knowledge-export `
  --output ".\output\issues.csv" `
  --format csv `
  --dataset issues
```

CSV Capability Gaps：

```powershell
python main.py knowledge-export `
  --output ".\output\capability_gaps.csv" `
  --format csv `
  --dataset capability_gaps
```

## 14. 第一次真实数据验收建议

不要一开始直接导入全部历史数据，建议：

```text
PLC 10~20条
  ↓
确认 Header / 字段映射
  ↓
确认 Raw 完整
  ↓
确认 NEW / UPDATED / SKIPPED
  ↓
Web Issues / Detail
  ↓
1~3条 AI
  ↓
人工检查 Occurrence / Escape / Recurrence / Gap
  ↓
Statistics / Export
  ↓
PLC 全量
  ↓
HMI 小样本 → 全量
  ↓
IFA 小样本 → 全量
```

## 15. Release Acceptance

正式 RELEASE 前必须验证：

- HMI / PLC / IFA 均可识别与导入；
- Raw 未修改；
- 产品特有字段不丢失；
- Where / Why Occurred / Why Escaped 不混淆；
- 重复导入可 SKIP；
- 更新形成新 Issue Version；
- AI Run 与 Issue Version 绑定；
- AI 失败不破坏事实数据；
- Evidence 可回溯；
- Web / CLI 一致；
- Statistics 正确；
- XLSX / CSV 可用；
- Existing Repeat Case Similarity / Decision 无回归。

目标链路：

```text
真实 Excel
 ↓
自动识别
 ↓
增量导入
 ↓
SQLite
 ↓
Web / CLI
 ↓
AI
 ↓
Occurrence / Escape / Recurrence
 ↓
Technical / Management / Governance
 ↓
Statistics
 ↓
Export
```

全部通过后再冻结：

```text
KNOWLEDGE_QUALITY_ISSUE_ANALYSIS_ENGINE_V1.0_RELEASE
```

## 16. 当前已知限制

1. HMI / IFA YAML 目前是基于冻结 Design 的初版，需要真实 Excel 校准。
2. PLC YAML 已结合当前真实 PLC 表头增强，但仍需全量文件验证。
3. YAML 配置已进入统一包；正式 RC1 仍应完成 Configurable Adapter / Header Normalizer 的代码接入，使字段维护真正做到“改配置、不改代码”。
4. 当前包是 Release Candidate，不应跳过真实业务数据 E2E 验收直接标记 Final Release。


## CONFIG1 RC1 修复说明

本包已将字段配置真正接入运行链路，修复此前“YAML 已打包但 Engine 未读取”的问题。

新增运行机制：

```text
Excel Raw Header
   ↓
HeaderNormalizer
   ↓
YAML detection / aliases
   ↓
Business Detection
   ↓
Adapter Mapping
```

当前 HeaderNormalizer 会处理：

- 普通空格；
- 全角空格；
- 换行；
- Tab；
- 中英文括号；
- `（×开发填写×）` 等字段说明。

例如：

```text
ITR 单号
ITR单号
ITR\n单号
```

均可用于识别 `business_issue_id`。

真实 PLC 表头烟测已验证：

```text
Header Row: 1
Business Type: PLC
Score: 69
Total: 1
New: 1
Failed: 0
Status: COMPLETED
```

完整工程回归：

```text
152 passed
0 failed
```

## RC2 修复说明

RC2 收口三类真实数据验收问题：

1. **AI-CONFIG-001**：Quality Issue AI 统一读取根目录 `config/model.yaml`，支持 `quality_issue_ai` 对 `ai` 节点做增量覆盖；配置路径会进入诊断输出。
2. **AI-DIAG-001**：新增 `knowledge-ai-check`，用于检查 enabled/provider/base_url/model/API Key 环境变量，并支持 `--live` 实际请求模型。
3. **FIELD-CONFIG-002**：HMI / PLC / IFA Adapter 改为 YAML 驱动。配置中已匹配的标准字段进入 typed DTO；暂未有 typed DTO 槽位的字段进入 `product_extension.typed_model_pending`，所有已配置且命中的字段同时保存在 `product_extension.configured_fields`。
4. **AI-PERSIST-001**：新增自动化测试验证 AI Analysis Run、结果、Capability Gap 写入 SQLite 后，重新创建 Repository 仍可查询。

### AI 调试

先执行：

```powershell
python main.py knowledge-ai-check
```

正常应看到：

```text
config_path: <项目>/config/model.yaml
enabled: true
provider: openai_compatible
base_url: http://127.0.0.1:8000/v1
model: dtcoder
api_key_env: acca
api_key_present: true
ok: true
```

然后进行真实连通性检查：

```powershell
python main.py knowledge-ai-check --live
```

最后再执行：

```powershell
python main.py knowledge-analyze --knowledge-id <KNOWLEDGE_ID>
```

> RC2 的 `model.yaml` 按当前已确认的本地模型服务预置为 `http://127.0.0.1:8000/v1` / `dtcoder`，API Key 仍只从环境变量 `acca` 读取，不在文件中保存密钥。

## RC3 — AI Response Robustness

RC3 fixes the case where the LLM returns a valid, human-readable JSON response but the Engine marks the analysis FAILED because nested EvidenceValue DTOs are stricter than the model output.

The runtime now uses:

```text
raw AI response
  → JSON extract/repair
  → response normalization
  → DTO validation
  → SQLite persistence
```

For troubleshooting, RC3 also persists per-run debug data in `analysis_run_debug`:

- `raw_response`
- `parsed_json`
- `normalized_json`
- `validation_error`

This allows distinguishing model failure from JSON extraction, normalization, schema validation, and persistence failures.

---

# RC4 — Analysis Workbench UI

RC4 将 Issue Detail 从 Raw/JSON 优先展示调整为业务分析结果优先展示。

详情页结构：

```text
问题事实
 ↓
为什么发生 / 为什么流出
 ↓
再发风险
 ↓
Technical / Management / Governance Gap
 ↓
Original / Normalized / Version / Run / Debug（折叠追溯）
```

Issues 列表新增：AI 状态、再发风险、T/M/G 能力缺口数量。

RC4 不修改 AI 分析算法和核心数据库模型。

## RC4 PATCH01 — Web Export Completion

Web Export 已补齐为可直接使用的浏览器功能：

- Export 独立页面；
- Issues 页“导出当前结果”；
- 全部问题 / 当前筛选结果；
- 问题知识 / AI 分析结果 / Capability Gaps；
- XLSX / CSV 浏览器直接下载；
- XLSX 工作表：Issue_Knowledge、AI_Analysis、Capability_Gaps、Statistics；
- CLI Export 保持兼容。

Web 使用：进入 `/export`，或在 `/issues` 筛选后点击“导出当前结果”。
