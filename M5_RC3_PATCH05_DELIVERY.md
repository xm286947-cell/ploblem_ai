# REPEAT_CASE_ENGINE V2.3 M5 RC3 PATCH05

## 修复内容

1. Query 运行产物清理
   - 每次完整查询在 M7.1 完成后，清理本次 Query 的历史下游产物。
   - 保留 `knowledge/raw_query` 原始输入。
   - ALL 模式以 `knowledge/raw_query` 为有效 Query 清单，自动清理其他目录中的孤儿 Query。

2. 综合置信度修复
   - 优先使用 M8.4 判定置信度。
   - 判定置信度为空或为 0 时，依次回退至 M8.2 `confidence`、`overall_score`、有效维度得分平均值。

3. 相似度评分展示
   - 推荐案例展示综合相似度、判断置信度和各维度得分。
   - 其他候选案例同样展示综合相似度、判断置信度和各维度得分。
   - 缺失得分显示“未评分”。

## 测试结果

- 全量测试：118 passed
