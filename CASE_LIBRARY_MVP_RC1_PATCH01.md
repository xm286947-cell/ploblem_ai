# CASE_LIBRARY_MVP_RC1_PATCH01｜案例库统一产品定位补丁

## 目标

把现有 RC1 的产品表达从“重大问题案例库”修正为：

> 一个案例库，多个知识来源；重大问题是第一条标准化知识生产通道。

## 变更性质

本补丁是产品边界与交付包装补丁，不改变运行语义。

### 改变
- RC1 交付说明
- Historical Case 公共契约说明
- 产品边界文档
- 完整包名称与 Manifest 产品标识
- CI Artifact 名称

### 不改变
- CASE-PUBLISH 运行代码
- `historical-case/v1` 版本
- Major SQLite Schema
- Major Revision
- Repeat Risk
- Runtime

## 应用结果

补丁应用并重新打包后，完整包命名为：

`CASE_LIBRARY_MVP_RC1_<commit>.zip`

包内 Manifest 明确：

- `product = CASE_LIBRARY`
- `product_positioning = ONE_CASE_LIBRARY_MULTIPLE_KNOWLEDGE_SOURCES`
- `source_channel = MAJOR_EVENT`
- `standalone_major_case_product = false`

内部 `major_case_*` 模块继续保留，仅表示重大问题来源适配器，不代表第二套案例库。
