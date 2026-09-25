# RC3-D2 Reviewed Specification 闭环接通

状态：开发实现完成 / 本地回归通过
日期：2026-09-19

## 目标

在 D1 单次抽取链路基础上，把事实候选无损接入 RC3 已有 Reviewed Specification → 人工确认 → Device Conclusion 产品闭环；不重做 Parser、数据库主模型或页面信息架构。

## D2 关键规则

1. Reviewed Specification 继续作为 Raw Candidate 的可重建语义归并层；pending 事实允许形成“草稿规格”，便于用户在默认页面直接核对。
2. 等价重复候选只要其中一条已被人工确认，该语义规格即视为 confirmed；不要求用户重复确认同一事实的每条重复证据。
3. Device Conclusion 的 `conclusion_status` 继续表示信息覆盖完整度，保持 RC3 兼容；新增 `verification_status` / `is_formal` 表示人工确认状态，避免把未确认抽取结果当成正式结论。
4. 正式结论门：所有当前 P0/P1 Reviewed Specification 已人工确认、关键字段无缺口、异常 Review Gate 已处理。
5. Review Gate 未处理时，即使候选已确认，也保持 `attention_required`，不能成为 formal conclusion。
6. Evidence 在 Raw Candidate → Reviewed Specification → Device Conclusion 传递时保留 `source_id`；不同来源即使页码/原文相同也不被错误去重。
7. UI 结论区明确显示“草稿 / 待人工确认”或“已人工确认，可作为正式结论”，并显示 Reviewed Specification 确认计数。
8. 证据链接优先使用 evidence 自身 `source_id`，单来源继续兼容器件主 source。

## 新增接口

`GET /api/devices/{device_id}/specification-status`

返回：
- `status`: no_specification / pending_confirmation / partially_confirmed / attention_required / confirmed
- `formal_ready`
- reviewed / confirmed / pending 数量
- missing / pending critical fields
- pending P0/P1 fields
- Review Gate 与 Final Review 状态

## 兼容性

- 不修改候选表、Reviewed Specification 表和 Device Conclusion 表结构。
- `conclusion_status=complete/partial/insufficient` 语义保持不变。
- 正式对比、知识查询仍只消费 confirmed candidate，不放宽正式数据边界。
- 人工 confirmed 值保护逻辑保持不变。

## 本地验收

- `python3 -m py_compile storage_life/*.py`：PASS
- `python3 -m compileall -q storage_life`：PASS
- `node --check`（从 index.html 提取脚本）：PASS
- `python3 -m pytest -q`：73 passed

D2 新增覆盖：
- 未确认关键规格时结论只能是 draft；确认后变 formal；
- 等价重复候选只需一次人工确认；
- 未解决 Review Gate 阻止 formal；
- Reviewed / Conclusion evidence 保留多 source provenance；
- specification-status API 与 UI confirmation state。
