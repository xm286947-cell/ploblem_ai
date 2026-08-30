# P2 RC1 多智能体动态使用 PATCH03

## 功能

- `config/model.yaml` 支持 `quality_issue_agents` 配置多个智能体。
- 支持 `quality_issue_agent_routing` 按发生、流出、再发、能力缺口四阶段动态路由。
- 单问题和批量 AI 分析均可选择“动态路由”或指定一个智能体。
- 指定智能体时覆盖四阶段路由；动态路由时各阶段独立选择。
- 每次分析在 `analysis_profile_json.analysis_agent` 记录实际智能体，模型名称继续写入运行历史。
- API 支持 `analysis_agent` 参数。

## 使用规则

- 智能体未填写的配置继承公共 `ai` 配置。
- API Key 只配置环境变量名，不写入 YAML。
- 已完成阶段默认复用；切换智能体后如需重跑，必须选择“强制重新分析”。
- 现有单智能体配置仍兼容，可使用“默认智能体”。

## 验证

- 专项回归：16 passed
- 正式测试目录全量回归：384 passed
- 数据库 Schema：无变化
