# 硬件知识库 UED R1｜独立 UI 演进与 Mock 验证

状态：`UI_IMPLEMENTED / MOCK_E2E_VALIDATION / PR_DRAFT`  
总 Issue：[575](https://github.com/xm286947-cell/ploblem_ai/issues/575)  
UED PR：[625](https://github.com/xm286947-cell/ploblem_ai/pull/625)  
冻结 UI 开发起点：`0e43522dfc3db4bd1a0ceb86b0bff26755ad5e69`  
UED 分支：`feature/hardware-ued-three-entry-20261011`  
职责边界：本分支只做 UED/Mock，真实环境 E2E 由 #613 保持独立。

## 当前代码事实与变更

| 旧版真实界面 | R1 主入口 | 原行为保护 |
|---|---|---|
| 主平台约 11 项菜单 + 硬件内层研发 7、维护 11 | 硬件区域收敛为 **找知识 / 看资产 / 知识维护** | 其他平台页面导航保持不变；平台菜单可展开 |
| 案例首页、案例搜索、正式知识消费多次出现 | 找知识；案例与问题 / 已发布工程知识作为上下文切换 | 原 `/search`、`/knowledge`、只读检索 API、正式知识契约不变 |
| 两棵树单列多个入口 | 看资产；页面内部电路/特性与物料/器件切换 | `/tree`、双树及其已确认关联不变 |
| Word 导入、知识导入、生产、确认分散 | 知识维护；上传 Word → 处理与候选 → 人工确认 | 保留 `/intake`、`/knowledge-production`、`/review`，Candidate 不等于 Review |
| 原型调试和 R1 E2E 混在导航 | 辅助工具折叠 | 旧 `/word-import`、`/e2e`、`/base-data` 保留可访问 |
| 检索错误与真正零结果混淆 | 明确“查询失败”，提供“重新查询” | 仅表现层；不修改后端、检索结果、Provider |
| 进入详情后返回丢失检索上下文 | 保留 `q` 和“返回找知识结果” | 搜索与详情原 URL 均不删除 |

## 现有 Mock 浏览器证据

- 仓库可重复运行脚本：`scripts/hardware_ued_browser_mock.py`。真实 FastAPI 产品服务 + Chromium 浏览器，拦截硬件业务 API，注入 `MOCK ONLY` 合成案例 A0152 与 A9999；隔离临时数据库和源文件根目录；禁止正式 Provider 与发布。
- 仓库 CI：`.github/workflows/hardware-ued-browser-mock.yml`，中文字体和浏览器依赖在 runner 初始化；以**准确 HEAD** 对应 workflow run 的 `RESULT.json`/截图与 CI 结论为证。未知 Mock API 使用 fail-closed，而非伪造 HTTP 200。
- 回归含三入口、中文检索、双向检索词保持、查询参数与案例详情返回、详情 Evidence 预览、抽屉关闭、双树切换、Word→Candidate→Review、390px 无横向溢出、接口 503 与重试；每次需按最终 CI 结果判定 PASS/FAIL。
- 当前会话额外有离线 Mock 的 10 项 PASS 截图；仅供 UED 设计参考，**不可替代真实产品页面 Mock 回归**。

## 安全与兼容

- 未更改 Python API 路由、后台 Schema、审核状态、正式知识/原文来源、Provider 配置和 Agent Prompt。
- 没有实际人工审核或 Publish；Mock review 页不能构成正式审核证据。
- 导航显示/隐藏**不是鉴权**；维护权限仍须由原 API 与真实用户认证保证。
- 由 #613 的 Codex E2E 和测试中心分别做授权 Provider/正式数据隔离/真实审核业务证明，不把 Mock 的通过合并进 Real Provider Gate。

## 交付阶段与遗留

1. **已实现代码**：三入口及上下文路径，兼容链接、响应式样式和独立 Mock 测试设施。
2. **正在闭环**：最新提交的 Native Chrome Mock、M3 前端及其关联 CI；需核最终报告与红灯根因。
3. **未实现**：搜索两类来源的单一结果流聚合；目前是一个业务任务入口下面两套只读结果页面，避免未经评审改动检索契约。
4. **未验证**：真实 macOS/Windows 人工 UAT、正式语料检索质量与真实 Provider；必须分别在既有 #575/#613 测试链验证。
5. **合并 Gate**：维持 Draft，不自动合并；最终由系统负责人依据确切 `BASE_SHA→HEAD_SHA`、受影响范围 CI、Mock 截图、真实 E2E 独立结果批准。
