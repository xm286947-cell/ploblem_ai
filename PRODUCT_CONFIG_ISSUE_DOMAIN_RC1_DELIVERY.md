# 产品配置与问题领域 RC1

## 已实现

- 补齐产品配置 API：列表、新增、更新、启用、停用、Mapping 状态。
- 放开 YAML Mapping Migration 的产品编码白名单限制，新产品可使用 `--business <PRODUCT_CODE>` 初始化。
- 产品配置菜单已合并到完整页面模板。

- 新增产品配置表与默认 IFA / PLC / HMI 配置。
- 新增产品配置页面，可新增或更新产品及默认问题领域。
- 新产品启动时注册为 YAML Mapping 驱动的通用适配器。
- Knowledge 问题版本增加 `issue_domain` 与 `issue_domain_source`，旧数据库自动兼容迁移。
- 导入支持问题领域预设；Excel 中的“问题领域 / 问题属性 / issue_domain”按行优先。
- 没有行级值时使用导入预设，再回退到产品默认值和 AI 自动判断。
- 新增批量问题领域 API：`POST /api/issues/batch-domain`，修改写入审计表。
- 分析运行在未显式选择分析领域时，会读取问题自身的 `issue_domain` 作为分析上下文。

## 兼容性

- 保留 HMI、PLC、IFA 原有编码和 Mapping 流程。
- 没有新增复杂产品关系模型；产品默认值不会覆盖问题自身属性。
- 历史问题无领域值时按 AUTO 处理。

## 验证

- Python compileall：通过。
- 当前环境未安装 pydantic / pytest，因此未执行完整测试套件。
