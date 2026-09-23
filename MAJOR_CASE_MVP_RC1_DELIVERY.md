# 案例库 MVP RC1｜重大问题来源通道交付说明

## 1. 产品定位

本 RC1 属于统一“案例库”产品。

重大问题不是另一套独立案例库，而是当前案例库已经打通的第一条标准化知识生产通道：

```
Major Knowledge（人工确认）
        ↓
CASE-PUBLISH-001
        ↓
Historical Case
        ↓
统一案例库
        ↓
historical-case/v1
        ↓
Search → Detail → Evidence
```

因此，本 RC1 的含义是：

> 案例库 MVP 已具备“重大问题来源通道”，能够把人工确认后的重大问题稳定沉淀成统一 Historical Case，并被统一检索、查看和证据追溯。

未来其他来源可继续通过标准发布契约进入同一个案例库，而不是新建第二套案例库。

## 2. 已包含

- 统一 Historical Case 资产
- `historical-case/v1` Consumer Contract
- Major Knowledge 独立 SQLite Repository / Schema（重大问题来源域）
- Event / Entry / Scope / Revision / Evidence
- CASE-PUBLISH-001-A：Major Event → Publish Candidate
- CASE-PUBLISH-001-B：幂等、原子发布
- CREATED / REUSED / UPDATED
- stable case_id
- EVENT 隔离
- CASE_SHARED 支持
- UNSCOPED 排除
- Evidence 追溯
- 发布失败 rollback / retry
- Golden Path smoke

## 3. 明确不包含

本 RC1 不把下列能力纳入案例库 MVP 发布范围：

- Repeat Risk 决策逻辑
- Major Web/UI
- Major AI 分析流程
- Runtime Provider
- 真实业务数据
- 本地密钥、API Key、Authorization
- input/output/knowledge 运行数据

这些能力可以作为上游生产、下游消费或公共基础设施独立演进，但不改变“一个案例库、多个知识来源”的产品边界。

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

## 6. 产品边界

详见：

`docs/product/CASE_LIBRARY_PRODUCT_BOUNDARY_V1.md`

## 7. 基线

本定位补丁基于：

`main@92d57ead247f17882e6bf3132ae9426fc80073f2`
