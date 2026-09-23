# Prompt V2 + Human Confirmation RC1

## IMPLEMENTATION_STATUS

COMPLETED — 四阶段分析已升级为轻量质量、系统与客户价值视角，并打通 AI 疑问到人工确认、重新分析和审计追溯。

## 核心能力

- Occurrence：引入阶段、系统边界、受影响组件、接口/版本约束、关键疑问。
- Escape：应发现阶段、实际发现阶段、缺失控制和控制失效类型。
- Recurrence：客户可见现象、业务流程、服务中断、安全/数据风险、规避方案和影响范围。
- Capability Gap：P0/P1/P2、首个动作、验证指标；全部维度合计最多 5 项。
- Evidence：无证据不得标记 EXPLICIT；INFERRED 必须显式表达不确定性。
- Human Confirmation：确认、修正、暂不确定、不适用；支持填写答案和证据。
- Reanalysis：人工确认作为 `human_confirmations` 回注四阶段分析，人工事实优先。

## DATABASE_CHANGES

- 新增 `analysis_open_question` 表，保存问题版本、分析运行、阶段、疑问、人工状态、答案、证据和确认人。
- `issue_capability_gap` 兼容增加 `priority`、`first_action`、`verification_metric`。
- 旧数据库启动时自动迁移，不覆盖历史分析结果。

## API

- `GET /api/issues/{knowledge_id}/analysis-confirmations`
- `POST /api/issues/{knowledge_id}/analysis-confirmations`

## TEST_RESULT

- Python compileall：通过。
- Response Normalizer smoke：通过。
- AI 疑问保存、人工确认、重新读取 roundtrip：通过。
- 当前运行环境未安装 pytest，因此完整 pytest 未执行。
