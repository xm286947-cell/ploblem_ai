# P2 RC1 问题级多智能体动态分配 PATCH07

## 正确语义

- 一个问题只分配一个智能体。
- 发生、流出、再发、能力缺口四阶段全部使用该智能体。
- 批量分析时，不同问题按启用智能体顺序轮询分配。
- 单问题分析时，根据 knowledge_id 稳定选择一个智能体。
- 页面手工指定智能体时，该问题固定使用指定智能体。

## 配置

只需要 `quality_issue_agents`，不需要 `quality_issue_agent_routing`：

```yaml
quality_issue_agents:
  minimax-2.7:
    enabled: true
    model: minimax-2.7
  qwen3.6-user:
    enabled: true
    model: qwen3.6-user
  deepseek:
    enabled: true
    model: deepseek
```

公共 `base_url`、密钥环境变量等继续从 `ai` 继承。

## 诊断

`GET /api/analysis-agents` 返回：

- `assignment_strategy: ROUND_ROBIN_BY_ISSUE`
- `eligible_agent_ids`: 当前参与动态分配的智能体
- `distinct_model_count`: 不同模型数量

## 验证

- 专项：18 passed
- 全量：386 passed
- 数据库 Schema：无变化
