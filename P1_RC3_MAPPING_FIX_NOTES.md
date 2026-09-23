# P1 RC3 字段映射修复

修复 ACTIVE Mapping 只能查看、无法进入可编辑 Draft 的问题。

操作流程：

1. 打开 `/p0/settings`。
2. 在“字段映射”选择产品。
3. ACTIVE 正式版本仍保持只读。
4. 点击“创建/打开可编辑 Draft”。
5. 系统基于当前 ACTIVE 创建下一版本 Draft，并保留已有源表头和启用状态。
6. 修改后依次执行“保存 Draft → Validate → Activate”。

新产品没有 ACTIVE Mapping 时，仍从 59 个统一标准字段创建 Starter Draft。

本次没有数据库 Schema 和 API 输出契约变化。
