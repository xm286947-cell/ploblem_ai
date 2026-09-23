# PATCH17 大量问题产品报告分层综合

## 实现结果

- 1～25 个已分析问题：单次直接综合。
- 超过 25 个：每 25 个问题生成一份批次摘要，再对批次摘要执行最终综合。
- 同一份报告从批次到最终综合固定使用同一个已启用 agent/model。
- 批次缓存以问题摘要内容、模型和 Prompt 版本计算哈希；相同内容再次生成时复用批次摘要。
- 最终报告保留真实 knowledge_id；越界或虚构的证据 ID 会被服务端删除。
- 报告记录 synthesis_strategy、synthesis_batch_count、synthesis_batch_size、synthesis_cache_hits、synthesis_agent、synthesis_model。
- 任一批次或最终综合失败时，报告标记 FAILED 且不能发布，避免发布不完整结论。

## 验证

- 61 问题专项：3 次 BATCH_MAP + 1 次 FINAL_REDUCE。
- 相同范围第二次运行：3 个批次全部命中缓存，只执行 1 次 FINAL_REDUCE。
- 全量测试：392 passed。

## 安装和启动

解压覆盖到 V2 完整包根目录，继续双击 start_quality_capability_p1.bat；无需 init 和数据库迁移。
