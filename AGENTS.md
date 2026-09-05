# 后续开发约束

- 用户指定后续执行模型为 GPT-5.6 Sol。交接文件不自动切换会话模型；非 Sol 承接时如实提示，不冒称已切换。不因此修改应用自身模型配置。
- 开始工作先完整阅读 `docs/requirements/REQ-020_CUSTOMER_QUALITY_SCENARIO_PORTRAIT_BASELINE_V1.md` 与 `docs/requirements/SOL_DEVELOPMENT_HANDOFF.md`，按其阶段推进并更新台账。新的明确用户要求优先。
- 复用当前质量分析系统，先核对现有实现；本轮不开发治理闭环，不重建前端/数据库。
- 所有 `sources/` 文件只读。不要访问或提交真实内部数据；优先合成数据与 SQLite。保留既有无关改动，严禁将运行数据、数据库、密钥和日志打包或提交。
- 保持 Win10 BAT 使用入口；未来 Linux/Docker/MySQL 兼容要求保留，但不在本轮实施迁移。用户代理是开发访问需求，不写入产品默认配置。
- CS/ITR 同问题复用、分组隔离、字段证据与人工确认不可破坏。AI 推断不得伪装事实，不能覆盖人工/发布结果。
- 不将 Mock 测试等同真实模型验收，不将 Mac 测试等同 Windows BAT 验收。交付必须列出未完成/未验证项。
