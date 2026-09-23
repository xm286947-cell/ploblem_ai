# P2 Acceptance Status

更新时间：2026-08-27

## 已完成验证

- 完整工程回归：`379 passed / 0 failed`。
- 验收文件：`outputs/quality_capability_p1/plc_quality_issue_full_fields_e2e.xlsx`。
- 工作簿范围：单 Sheet、59 个源字段、5 条 PLC 质量问题。
- 在全新临时 SQLite 数据库中执行 Mapping 初始化与正式 Import。
- Mapping Coverage：59 个源字段全部处理；49 个结构化字段、10 个扩展字段、0 Raw Only、0 Unmatched，覆盖率 100%。
- Import：5 New、0 Updated、0 Skipped、0 Failed，状态 `COMPLETED`。
- 数据库核对：`quality_issue=5`、`quality_issue_version=5`、`issue_source_raw_v1=5`。
- 样本包含软件变更、版本兼容、eMMC/掉电可靠性、硬件批次与温度、已知修复未合入发布分支。
- 验收期间修复 Mapping 校验重复告警：PLC 的同一 Source Header/Alias 歧义由重复 2 条收敛为 1 条真实警告；复验结果 HMI `0/0`、PLC `1/1`、IFA `0/0`（warning/conflict），全部 `invalid=0`。

## 已实现并通过自动测试

- AI 原生 MRC、生命周期、问题标签、硬件关联契约。
- 人工确认成为当前有效结论，并更新洞察。
- 软件问题关联多个器件、板卡、型号、失效模式、失效机理和失效原因。
- MRC×能力、生命周期×能力矩阵及服务端精确下钻。
- 产品/业务、月份、问题领域、生命周期联合筛选。
- 原始根因/测试 L1-L4 与当前 MRC 的一致、冲突、补充和缺失对照。
- 批量任务 SQLite 持久化、逐问题耗时/错误/失败阶段及只重试失败项。

## 当前外部阻塞

`knowledge-ai-check` 结果：模型已启用，但环境变量 `acca` 未设置，因此真实模型连通性和五条样本的四阶段 AI 输出尚不能验收。此项不得以模拟结果替代。

完成真实 AI 验收所需条件：

1. 在运行环境设置 `acca`。
2. 确认 `http://127.0.0.1:8000/v1` 可访问且提供 `dtcoder` 模型。
3. 对五条 PLC 样本以并发 2 运行四阶段分析。
4. 核对每条问题的发生/流出 MRC、能力标签、硬件关联、证据、置信度和待确认事项。
5. 由质量人员确认关键矛盾是否具有业务价值。

## 尚待交付收口

- 真实模型具备后执行五条 PLC 样本 AI E2E。
- 根据真实输出修订 Prompt/词典中的实际问题。
- 生成最终仅变化文件补丁包和完整 Release 包。
