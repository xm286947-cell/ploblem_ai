# 硬件 R2 Agent 智能检索 / 工程知识消费｜隔离环境试用说明

状态：内部开发与提测，**不是正式产品发布**。S1 PR #590，S2 依赖 PR #593，正式历史知识仅只读使用。参考 #584、#586、#587。

## 一、在哪里使用

- **案例搜索** `/p0/hardware-cases/search`：输入故障现象、案例编号或完整工程问题。结果展示真实命中模式（案例编号直达 / OpenSearch / 正式知识关键词 / 在线 Agent / 降级）。点击案例查看详情、Evidence 与原 Word。
- **正式知识消费** `/p0/hardware-cases/knowledge`：检索已发布正式知识后，按「研发设计复用 / 器件与电路风险 / 市场与应用问题 / 测试验证」视角看真实字段；S2 的「分析工程经验（试验）」是**点击后才会调用**工程分析 Agent，建议需要工程师判断，不是正式设计规范。

## 二、运行与配置前提

1. 使用独立 NON_PROD data root + 获准脱敏 A0152/A0207 正式知识投影、Evidence 与 Word；不操作正式库、不自动 Publish。
2. Query Agent 是显式开关 `HARDWARE_QUERY_AGENT_ENABLED=1`，通过已有 `HARDWARE_CASE_MODEL_CONFIG` 指向受控且授权的非生产 Model Profile（缺省路径 `config/runtime/model.local.yaml`）。新 Agent 定义在 `config/runtime/agents/hardware_retrieval.query_understand.yaml`。
3. S2 工程分析需要再显式设置 `HARDWARE_R2_AGENT_NONPROD=1` 和 `HARDWARE_ENGINEERING_AGENT_ENABLED=1`；只有 NON_PROD 才允许启用工程消费调用。
4. 模型凭据只能通过原有安全配置管理，禁止写入测试报告、GitHub、Prompt 或打包。发布 ZIP **不携带 model.local.yaml**；缺少配置必须显示 Agent BLOCKED/降级。
5. Real Provider 验收要求请求的 Runtime Task/Run/Provider/model/calls/token（若有）及耗时留脱敏轨迹；Mock PASS 不等于 Real Provider PASS。

## 三、必测操作（实际 Chrome 两入口）

| 工程师输入 | 正确业务目标 |
|---|---|
| 模拟量、ADC参考源、模拟量偏差 | A0207 |
| 串口乱码、MCU、A0152 | A0152 |
| A0207 | 精确定位 A0207 |
| 设计模拟量电路时，有什么经验可以借鉴？ | Query Agent 理解意图；召回 A0207，列出真实 WHY_HIT |
| 复位问题 | 只有当前两条正式知识时应为无依据零命中 |

在正式知识消费中选择 A0207 → 研发设计复用 →「分析工程经验（试验）」→ 核对输出仅使用工程规则/设计约束/验证方法等真实来源字段。每条显示字段原文摘录和**案例级 Evidence**；投影尚无字段到单条 Evidence 的权威绑定，不得将案例证据假称为逐字段证明。

## 四、门禁与已知限制

- S1 CI `s1-regression`：确定性召回、精确编号、阴性查询、Agent 运行轨迹与 Mock Provider 合同。
- S2 CI `s2-evidence`：来源字段、Evidence ID、Runtime Trace 的 Fail-Closed、实际 API/前端脚本检查。
- 短关键词可以走快速规则检索，**没有在线 Agent 调用不属于失败**；自然语言/工程建议要独立验证真实 Provider Task/Run，不能用响应时间判断 AI 真实性。
- 知识消费建议是**受控非生产参考**，不能改正式知识、写入旧库、发布、生成未确认工程规范。无合法 Provider 配置时正式知识仍可阅读，工程分析明确 BLOCKED。
- #577 Evidence UI 仍是独立 OPEN PR，#555 W3 仍 Draft；旧 Word Import 两条 400 用例另由 TSE #559/#572 收口，不能以 Mock green 跳过 Release Gate。
