# QUALITY ENGINE 需求池

最后更新：2026-08-31

## 当前路线

```text
稳定基线
→ 真实数据 E2E 与分析质量评估
→ 质量治理闭环
→ 正向风险保证
→ Docker/Linux/MySQL 规模化部署
```

## 需求目录

| 编号 | 需求 | 状态 | 优先级 | 建议阶段 | 独立说明 |
|---|---|---:|---:|---|---|
| REQ-001 | 稳定发布基线、Git Tag、可重复完整打包 | DONE | P0 | 当前 | `BASELINE_V1.1_P2_RC1.md` |
| REQ-002 | PLC/HMI/IFA/机器人/视觉真实数据全链路 E2E | TRIAGED | P0 | 近期 | 待细化 |
| REQ-003 | AI 专家评估集与 Prompt/模型回归门禁 | TRIAGED | P1 | 近期 | 待细化 |
| REQ-004 | 治理行动、责任人、措施、证据和效果验证闭环 | TRIAGED | P1 | 中期 | 待细化 |
| REQ-005 | 正向风险案例库及需求/设计/测试材料风险评估 | TRIAGED | P1 | 中期 | 待细化 |
| REQ-006 | Docker + Linux + MySQL 正式部署 | TRIAGED | P2 | 部署准备 | `REQ-006_DOCKER_MYSQL_LINUX_DEPLOYMENT.md` |
| REQ-007 | 正式环境版本化升级、备份、迁移和回滚 | TRIAGED | P0 | 部署准备 | `REQ-007_PRODUCTION_UPGRADE_AND_ROLLBACK.md` |
| REQ-008 | SQLite/MySQL Repository 隔离与 Alembic 迁移体系 | TRIAGED | P1 | 部署准备 | `REQ-006_DOCKER_MYSQL_LINUX_DEPLOYMENT.md` |
| REQ-009 | Web 与 AI Worker 分离、任务持久化及失败恢复 | TRIAGED | P2 | 规模化 | 待细化 |
| REQ-010 | 用户、业务、产品、配置和数据权限及操作审计 | TRIAGED | P1 | 上线前 | 待细化 |
| REQ-011 | 附件/报告/证据独立存储、备份与生命周期管理 | TRIAGED | P2 | 上线前 | 待细化 |
| REQ-012 | 模型成本、Token、耗时、失败率和分析质量监控 | TRIAGED | P2 | 运营期 | 待细化 |
| REQ-013 | 低质量批量问题的数据集隔离、独立分析与人工纳入 | TRIAGED | P1 | 近期 | `REQ-013_BATCH_ISSUE_DATASET_ISOLATION.md` |
| REQ-014 | 批量市场问题驱动的软件质量标准化识别与建设闭环 | TRIAGED | P1 | 近期专项 | `REQ-014_BATCH_ISSUE_STANDARDIZATION.md` |
| REQ-015 | 多类质量问题数据分层、关联、可信度和分析边界 | TRIAGED | P0 | 数据治理 | `REQ-015_QUALITY_DATA_CLASSIFICATION_AND_GOVERNANCE.md` |
| REQ-016 | ITR质量运行指标、MTTR分段与薄弱环节识别 | READY | P0 | 第二阶段 | `REQ-016_ITR_QUALITY_OPERATION_AND_MTTR.md` |
| REQ-017 | 问题分析驱动的标准识别、固化与效果验证闭环 | TRIAGED | P1 | 第三阶段 | `REQ-017_ANALYSIS_TO_STANDARDIZATION_CLOSED_LOOP.md` |
| REQ-018 | ITR与彻底解决单接入、自动关联和统一问题视图 | READY | P0 | 短期MVP | `REQ-018_ITR_AND_CS_INTAKE_MVP.md` |

## 已明确但暂不实施

- 当前 Win10 本地开发继续使用 SQLite 和 BAT，暂不强制使用 Docker/MySQL。
- 当前不把飞书数据库作为正式数据库替换方案。
- 上线后不再采用覆盖源码或叠加零散 Patch 的升级方式。
- MySQL 迁移必须在脱敏数据上先完成演练，不能直接在生产数据库试错。

## 新需求入口

后续聊天中出现的新诉求，先追加到本表。若未明确优先级，默认状态为 `IDEA`，不得因当前不开发而遗漏。
