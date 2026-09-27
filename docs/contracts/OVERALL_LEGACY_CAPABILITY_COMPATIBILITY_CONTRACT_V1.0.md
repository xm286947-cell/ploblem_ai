# Overall Legacy Capability Compatibility Contract V1.0

状态：`ARCHITECTURE_DECIDED / IMPLEMENTATION_PENDING`

适用范围：Overall VNext 统一 P0 Web Runtime 中继续提供历史稳定能力 `/analysis`、`/import`、`/statistics`。

## 1. 决策摘要

三个历史能力继续由当前 P0 Web 进程提供，保留原 URL、模板、表单/API 契约、服务和业务语义。实现方式是在 `create_p0_app(...)` 所创建的同一个 FastAPI host 中注册 Legacy Quality Issue 的 `APIRouter`；不得挂载第二个 FastAPI/Starlette 子应用，也不得启动第二个端口或进程。

Legacy Quality Issue Repository 必须使用显式绑定的 Legacy SQLite 文件。该文件与传给 `create_p0_app` 的 P0 主库是两个不同的规范化文件路径。Legacy Repository、P0 Repository、初始化器和迁移器均不得交叉打开对方的数据库。禁止通过表名前缀、同库共存、复制空表、运行时映射或跳转到近似页面来绕过 Schema 冲突。

本契约确定目标架构，不代表三个入口已经进入 P0 host。本分支仅交付契约；代码接入与运行验收需由后续实现任务完成。

## 2. 代码现状与根因

- `quality_knowledge.web.p0_app.create_p0_app` 是当前 Overall/P0 的 FastAPI 组合根；P0 质量问题能力使用 `P0Repository(db_path)`，启动时由 `P0Initializer.verify_ready(db_path)` 校验 P0 数据库。
- `quality_knowledge.web.app.create_app(db_path)` 另建 `FastAPI`，直接构造 `IssueKnowledgeRepository`、`QualityCapabilityExtension`、`BatchAnalysisJobManager`、Mapping/Product/Human Analysis repositories 和 Legacy Product Report services，并在同一个 `db_path` 上读写旧版 Schema。
- Legacy `create_app` 还注册 `/static` mount、Legacy 模板和大量旧路由。直接把这个 app mount 到 P0 app 会形成第二个 ASGI 应用边界；把 P0 `db_path` 传给它则可能触发旧库初始化并造成 Schema 冲突。
- Overall shell 是 transport/UI 层，不得直接导入 Legacy Repository 或其内部模型。
- P0 页面目前使用 `/p0/static/{asset_name}`；Legacy 页面通过 `url_for('static', path=...)` 使用 `/static`。接入后须保持其既有资源 URL 可解析，且不能覆盖 P0 asset 路由。

## 3. 能力边界与冻结语义

### `/import`

保留旧版导入页面和工作流：文件上传、预检、字段 Mapping、差异处理、用户确认、正式导入、批次结果查询，以及现有 API 入口。原有文件格式校验、Mapping 版本冲突、必填字段/冲突阻断、批次幂等行为和确认后写入规则不变。正式导入只写 Legacy 数据域，不自动写入 P0 Schema。

### `/analysis`

保留问题列表、单条分析、批量分析、失败重试、Agent 配置展示、执行状态/历史查询及现有 API。分析输入、分析结果、人工确认与能力投影仍由原 Legacy 服务定义；AI 执行器可由当前执行配置注入，但不得改变已保存结果和人工结论的优先级。

### `/statistics`

保留过滤条件、统计与能力缺口展示、公共缺口查询和 API。当前页面对筛选范围内问题执行的确定性能力投影回填属于已有行为，接入后须继续使用 Legacy repository/extension，并且只写 Legacy 数据库；不得在 P0 主库上执行此回填。

### 必须一并保持的路由依赖

以上页面依赖的 Legacy 页面资产、模板、服务、会话文件、Mapping/Product/Human Analysis 配置、问题详情、导出以及相关 `/api/...` 路由必须按实际调用链一并注册。迁移范围以当前 `quality_knowledge/web/app.py` 的路由与模板引用为基准，不得只让三个 GET 页面返回 200，却遗漏页面操作链。

以下应保留原 URL 与 HTTP 行为，包括但不限于：

- 导入：`/import`、`/import/preview`、`/import/confirm`、`/imports/{batch_id}`、`/api/import/*`、`/api/issues/import`。
- 分析：`/analysis`、`/analysis/{knowledge_id}`、`/analysis-batch*`、`/api/analysis*`、`/api/issues/{knowledge_id}/analyze`、分析历史与批次状态接口。
- 统计：`/statistics`、`/api/statistics`、`/api/common-capability-gaps`、`/api/capability-gaps`。

完整接入清单由后续实现任务基于冻结版本的 Legacy router 路由注册表和模板表单/API 引用生成并验收。不得将 Legacy `/` 注册到 P0 host；Overall 的 `/` 继续按当前正式 P0 入口规则工作。

## 4. 组合与数据库契约

```text
单一进程 / 单一端口
└── create_p0_app(P0_DB, legacy_quality_issue_db_path=LEGACY_DB)
    ├── P0 routers/services ──> P0_DB
    ├── Legacy Quality Issue APIRouter/services ──> LEGACY_DB
    └── Overall Shell ──> UI/navigation only
```

1. `create_p0_app` 是唯一 Web composition root。Legacy 接入形态为 router/service factory，不是 `app.mount(...)`、反向代理、iframe 或第二个服务。
2. `legacy_quality_issue_db_path` 是新增的显式组合输入；路径不得省略后猜测、不得默认等于 P0 `db_path`、不得因缺失而静默新建空 Legacy 库。
3. 两个路径经 `resolve()`/等价规范化后必须不同；违反时在启动阶段 fail closed，并给出稳定诊断码 `LEGACY_P0_DATABASE_PATH_COLLISION`。符号链接和相对路径也按解析后的实际目标比较。
4. Legacy DB 不可用、Schema 不兼容或未绑定时，三个 Legacy 能力应显示明确不可用状态并返回稳定 503/诊断信息；不得伪造空统计、空导入批次或把请求回退到 P0 repository。
5. Legacy DB 的创建、升级、备份和迁移仍由原 Legacy 数据库生命周期拥有者执行。Web 组合层只验证绑定与就绪状态；不得在部署启动时把 P0 初始化器或 Legacy Repository 指向另一域数据库。
6. Legacy 表、文件会话、上传文件和导出文件仍归 Legacy 域所有。Overall Shell 不直接读表，不把两个数据库做跨域 SQL join；确需跨域消费时必须另行定义稳定的公开契约和独立任务。
7. 两域数据不会因路由接入而自动复制、同步或合并。`knowledge_id` 的相同字符串不表示跨库实体相同。

## 5. URL、模板与静态资源契约

- `/analysis`、`/import`、`/statistics` 及其同域旧版辅助路由继续在同源下可直接打开，不用 redirect 到 P0 的近似功能。
- 保留 Legacy 模板使用的路径、表单 action、query 参数、重定向状态码和 JSON contract。只允许为 router 注入依赖或调整模板渲染请求对象；不得改变用户可见流程和业务含义。
- Legacy `url_for('static', path=...)` 的资源在统一 host 中必须可用。静态文件注册不得截获 `/p0/static/*`，也不得要求用户启动第二套静态 Web。
- Overall 导航可提供指向三个原路径的入口；导航不得持有 Legacy 数据逻辑。若能力未绑定，入口须报告明确不可用，不可链接到语义不等价页面。
- P0 的 `/`、`/p0/issues` 和 PR #209 的 URL 状态绑定保持不变。Legacy 不注册冲突根路由。

## 6. Runtime / 执行器边界

Web 统一只定义进程、路由、配置注入和生命周期，不意味着将 Legacy 数据模型迁移进 P0，也不意味着重写旧分析能力。`/analysis` 仍使用其已有分析服务和既有 AI 执行入口；执行器配置由组合根显式注入，并沿用现有 secret/config 管理。新建第二套 Runtime 管理面、第二套 Agent 状态机或第二套前端均不在本契约范围内。

如果既有 Legacy 执行器不能在当前 P0 运行包中加载，应让 Legacy 能力明确不可用并报告具体缺失依赖/配置；不得临时切换成不同分析逻辑冒充兼容。

## 7. 禁止项

```text
SECOND_WEB_APP=NO
SECOND_WEB_PROCESS_OR_PORT=NO
LEGACY_DB_EQUALS_P0_DB=NO
CROSS_SCHEMA_SQL=NO
SILENT_LEGACY_DB_CREATE_OR_MIGRATE=NO
SEMANTICALLY_INCORRECT_REDIRECT=NO
P0_REPOSITORY_FALLBACK_FOR_LEGACY=NO
AUTOMATIC_CROSS_DB_COPY_OR_SYNC=NO
LEGACY_ROOT_ROUTE_OVERRIDE=NO
OVERALL_SHELL_DOMAIN_REPOSITORY_ACCESS=NO
```

## 8. 实施验收 Gate

后续代码任务至少通过以下专项验证；这些验证不等同完整产品回归：

1. **单 Host**：启动一次 `create_p0_app`；确认 `/analysis`、`/import`、`/statistics` 由同一 FastAPI host 响应，启动进程/端口数量不增加，P0 根入口与 `/p0/issues` 不变。
2. **路径隔离**：配置两个不同 SQLite 文件；启动时记录并断言 repository 实际路径。P0 初始化状态/表集合不因 Legacy 启动改变，Legacy 初始化/导入/分析/统计投影不写 P0 DB。
3. **防误绑**：Legacy 路径省略、解析后与 P0 路径相同、Legacy DB 缺失或 Schema 不兼容时，启动或 Legacy 能力按本契约 fail closed；无静默建库、空结果回退和跨库写入。
4. **页面能力**：三个原入口及静态资源可达；以合成旧版数据验证导入预检→确认→批次查看、分析页面→执行状态/历史、统计过滤→原统计/能力缺口展示。不得用测试专用 API/DB 造候选页面结果代替用户操作。
5. **合同回归**：保留旧页面表单 action、重定向、主要 HTTP 状态和 API JSON 字段；P0 的 `/`、`/p0/issues`、PR #209 的 Query/Filter/Page/Selected ID URL binding 与现有 P0 页面回归通过。
6. **边界检查**：确认不创建第二个 FastAPI app/mount/service，不新增 schema 合并、不做语义不等价 redirect，不改 P0 / Legacy 业务含义。

## 9. 后续实施顺序

```text
本兼容契约评审
→ 单独实现任务：Legacy router/service factory 与显式 Legacy DB binding
→ Legacy 三入口及操作链专项集成验证
→ Overall Frontend Parent Integration Gate
→ 再由负责人决定是否纳入 Overall DUT / TSE 正式验证
```

契约评审通过前，不应声称这三个入口已恢复可用。实现完成后须单独记录各入口的可用状态和未验证项；这份文档本身不关闭运行时接入缺口。

