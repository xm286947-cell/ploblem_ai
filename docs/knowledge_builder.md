# Knowledge Builder

M6将`enriched_case`转换为四类检索资产：

1. Retrieval Document：标准化检索文本与结构化过滤字段
2. Embedding：语义向量
3. Index：面向过滤和快速定位的JSONL索引
4. Manifest：知识库版本、模型和文件清单

默认`local_hash`适合离线验证，不代表最终语义检索效果。生产环境应配置真实Embedding模型。
