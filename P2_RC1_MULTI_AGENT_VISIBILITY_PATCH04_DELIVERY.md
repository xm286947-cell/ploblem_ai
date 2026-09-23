# P2 RC1 多智能体可见性与同模型诊断 PATCH04

## 根因

PATCH03 示例中的 `quality` 与 `fast` 虽然是两个智能体，但都配置为 `model: dtcoder`，因此动态路由选择了不同智能体，实际请求仍使用同一模型。历史页面此前只显示接口返回的模型名，也无法看见智能体 ID。

## 修复

- 批量分析页解析四阶段实际路由；若所有路由最终指向同一 `base_url + model`，显示明确警告。
- 分析历史同时显示智能体 ID 和模型名称。
- 旧运行记录没有智能体审计字段时显示 `DEFAULT`。
- 增加不同模型动态路由的契约测试。

## 正确配置示例

```yaml
quality_issue_agents:
  quality:
    label: 质量深度分析智能体
    model: your-deep-model
  fast:
    label: 快速分析智能体
    model: your-fast-model

quality_issue_agent_routing:
  occurrence: quality
  escape: quality
  recurrence: fast
  capability_gap: quality
```

模型名必须是当前 OpenAI-compatible 服务真实支持的模型。修改后需重启服务；如需用新模型重跑已完成阶段，需要勾选“强制重新分析”。

## 验证

- 专项：17 passed
- 全量：385 passed
