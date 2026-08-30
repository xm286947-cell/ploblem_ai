# PATCH18 默认模型与删除草稿

## 行为

- 产品综合报告固定读取 model.yaml 的默认模型配置，不参与单问题多智能体轮询。
- 大范围报告的所有 BATCH_MAP 与 FINAL_REDUCE 均使用该默认模型。
- REVIEW_REQUIRED 未发布报告显示“删除草稿”，二次确认后删除报告及其版本记录。
- PUBLISHED 已发布报告不显示删除按钮；直接调用删除接口也返回 409：PUBLISHED_REPORT_CANNOT_BE_DELETED。
- 批次摘要缓存不随报告草稿删除，后续相同范围仍可复用，减少模型消耗。

## 验证

- 默认模型身份专项通过。
- 草稿删除、已发布禁止删除专项通过。
- JavaScript 语法检查通过。
- 全量回归：394 passed。

## 启动

覆盖 V2 完整包后继续双击 start_quality_capability_p1.bat，不需要 init 或数据库迁移。
