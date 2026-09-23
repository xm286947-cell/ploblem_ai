# PATCH37 缺失模块热修说明

## 问题

PATCH36 更新了 `quality_knowledge/repositories/v1_repository.py`，该文件依赖 `quality_knowledge.issue_period`。该依赖在完整包中存在，但没有被 PATCH36 显式加入覆盖升级包；旧安装目录缺少该文件时，使用 BAT 启动会报 `No module named 'quality_knowledge.issue_period'`。

## 修复

- PATCH37 显式包含 `quality_knowledge/issue_period.py`。
- 保留 PATCH36 的全部 SQLite 性能优化。
- 不修改数据库、BAT、Python 环境及业务数据。

## 升级

停止服务后，将 PATCH37 内容按原目录结构覆盖到 PATCH36 安装目录，再使用原 BAT 启动。无需回退 PATCH36。
