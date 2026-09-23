# 重大问题案例库 MVP RC1 交付说明

## 1. 交付目标

本 RC1 只收口一条真实业务链路：

```
Major Knowledge（人工确认）
        ↓
CASE-PUBLISH-001
        ↓
Historical Case
        ↓
historical-case/v1
        ↓
Search → Detail → Evidence
```

MVP 的完成标准不是功能数量，而是能够把一条已确认重大问题稳定沉淀成 Historical Case，并被统一消费契约查询、读取和证据追溯。

## 2. 已包含

- 独立 Major Knowledge SQLite Repository / Schema
- Event / Entry / Scope / Revision / Evidence
- CASE-PUBLISH-001-A：Major Event → Publish Candidate
- CASE-PUBLISH-001-B：幂等、原子发布
- `historical-case/v1` Consumer Contract
- CREATED / REUSED / UPDATED
- stable case_id
- EVENT 隔离
- CASE_SHARED 支持
- UNSCOPED 排除
- Evidence 追溯
- 发布失败 rollback / retry
- Golden Path smoke

## 3. 明确不包含

RC1 不把下列能力作为重大问题案例库 MVP 的发布范围：

- Repeat Risk 决策逻辑
- Major Web/UI
- Major AI 分析流程
- Runtime Provider
- 真实业务数据
- 本地密钥、API Key、Authorization
- input/output/knowledge 运行数据

## 4. 快速验证

Windows：

```bat
run_major_case_mvp_smoke.bat
```

Linux / macOS：

```bash
bash run_major_case_mvp_smoke.sh
```

预期输出至少包含：

```
RESULT=PASS
GOLDEN_PATH=Major Confirmed -> Publish -> Search -> Detail -> Evidence
CASE_ID=...
EVIDENCE_COUNT=>0
```

## 5. Release Gate

RC1 只有同时满足以下条件才允许标记为可交付：

- Historical Case Contract tests PASS
- CASE-PUBLISH Adapter tests PASS
- CASE-PUBLISH Publisher tests PASS
- Golden Path smoke PASS
- 包完整性检查 PASS
- SHA256 已生成
- 不包含真实业务数据库、运行数据或 Secret

## 6. 基线

进入 RC1 收口开发时，Major Knowledge + CASE-PUBLISH-001 已合入 main：

`main@047e82e11611d1b1b8ca29f4b24bca4aadc26f9e`
