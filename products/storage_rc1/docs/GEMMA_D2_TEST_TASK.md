# RC3-D2 Gemma 独立测试任务

角色：独立测试方。READ ONLY / TEST ONLY。不得修改源码、测试、数据库基线或 Golden。

## 目标
验证 D2 是否真正接通：
`D1 candidate/evidence → Reviewed Specification → 人工确认 → Device Conclusion`
并确认未确认事实不会被包装成正式结论。

## 必测

1. SHA 与测试对象确认。
2. `python3 -m py_compile storage_life/*.py`。
3. `python3 -m pytest -q`，期望 73 passed；数量不一致先核对收集环境。
4. 静态追踪 `rebuild_reviewed_specifications → specification_workflow_status → get_device_conclusion / family_view`。
5. 验证 pending 规格：`conclusion_status` 可表示 coverage，但 `is_formal=false`，`verification_status=pending_confirmation`。
6. 验证 P0/P1 全部人工确认且关键字段齐全后 `is_formal=true`。
7. 验证等价重复候选：一条 confirmed 足以使合并后的 Reviewed Specification confirmed，证据仍全部保留。
8. 验证 extraction Review Gate 未处理时，即使人工确认完成也不得 formal；Final Review 为 `ready_for_human_review` 后才可解除 gate。
9. 验证 source provenance：不同 `source_id` 的相同 quote/page 不得在 Reviewed/Conclusion 层被错误去重。
10. 验证 UI 明确区分“草稿，待人工确认关键规格”与“已人工确认，可作为正式结论”。
11. 验证原正式对比/知识查询仍只消费 confirmed candidate。
12. 回归人工 confirmed 值不会被 Final Review 覆盖。

## 反假阳性

- 不只检查 helper 单测；确认正式 API/调用链使用 workflow 状态。
- 不允许 `is_formal` 写死。
- 不允许用 `conclusion_status=complete` 冒充人工确认完成。
- 不允许为了 formal 自动把 pending 改 confirmed。
- 不允许 evidence 去重丢 source_id。

## 输出
只允许：PASS / FAIL / BLOCKED。
FAIL 必须给 requirement / expected / actual / command / file / symbol-or-line / evidence / impact。
若存在关键架构争议，标 `CODEX_REVIEW_REQUIRED=YES`；否则 NO。
