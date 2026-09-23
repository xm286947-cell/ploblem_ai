# UED Common Gap Closure RC2 Delivery

## IMPLEMENTATION_STATUS

COMPLETED — 问题工作台数据正确性、共性能力缺口精确下钻、动态产品联动、统计性能与 UED 闭环已完成。

## IMPLEMENTED

- 问题工作台总数不再受默认 100 条查询上限影响，并增加分页与准确的当前范围提示。
- 共性缺口下钻同时使用能力维度、缺口类别和小规模精确 ID 集合，打开的均为真实关联问题。
- 共性缺口“关联问题”按 Knowledge ID 去重，避免跨业务复用问题编号时误计。
- 选择某一业务表示“查看涉及该业务的缺口”，聚合仍保留全局跨产品覆盖关系。
- 质量洞察与共性缺口筛选改为读取产品配置，新增机器人、视觉等产品可自动出现。
- 统计概览改为数据库聚合，移除逐问题读取分析与人工记录的 N+1 查询。
- 共性缺口增加“产品内共性 / 跨产品共性”可见标识，筛选页签保留现有范围。
- 新增共性能力缺口治理清单 CSV 导出，与普通问题明细导出区分。
- 更新静态资源版本，避免浏览器继续使用旧页面样式。
- 修正 requirements 中不可安装的 httpx2 版本范围。
- 恢复再发风险分析器提示词兼容标识，以及能力缺口每个维度最多 3 项的既有约束。

## DATABASE_CONTRACT_CHANGES

- 数据库 Schema：无变化。
- Knowledge 主结构：无变化。
- Mapping Schema：无变化。
- 新增查询能力：支持 `offset`、精确 Knowledge/业务问题 ID 集合，以及能力维度 + 缺口类别语义过滤。
- 新增 Web 导出：`GET /export/common-gaps`，返回 UTF-8 BOM CSV 治理清单。

## TEST_RESULT

- UED/共性缺口专项与相关回归：19 passed。
- 完整工程回归：267 passed，0 failed。
- Python 语法与 compileall：通过。

## COMPATIBILITY_RESULT

- 历史 SQLite 数据无需迁移。
- 原 `/issues?limit=` 参数继续兼容。
- 原问题明细导出继续保留；新增治理清单导出互不覆盖。
- HMI / PLC / IFA 继续使用原配置；新增产品由产品配置动态联动。

## PATCH_FILES

- `requirements.txt`
- `quality_knowledge/analyzers.py`
- `quality_knowledge/prompts/recurrence.md`
- `quality_knowledge/repositories/v1_repository.py`
- `quality_knowledge/services/knowledge_issue_service.py`
- `quality_knowledge/web/app.py`
- `quality_knowledge/web/statistics_presenter.py`
- `quality_knowledge/web/templates/base.html`
- `quality_knowledge/web/templates/issues.html`
- `quality_knowledge/web/templates/statistics.html`
- `quality_knowledge/web/static/app.css`
- `tests/test_ued_common_gap_closure_rc2.py`
