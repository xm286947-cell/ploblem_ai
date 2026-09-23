# REPEAT_CASE_ENGINE V2.1 Sprint-3 Patch-01

## 新增

- `config/repeat_case.yaml`：一键分析与Workspace统一配置。
- `common/config_loader.py`：统一YAML配置加载、缓存、路径解析和错误处理。
- `common/workspace.py`：运行目录自动创建与Knowledge就绪检查。
- `analyze.py`：从新问题Excel到重复问题报告的一键执行入口。
- `run.bat`：Windows双击运行入口。

## 修复

- 修复缺少统一运行配置的问题。
- 修复每次运行前需要手工准备目录的问题。
- 修复新问题分析需要逐阶段手工执行的问题。
- Knowledge未初始化时改为明确提示，不输出长Traceback。

## 兼容性

- 不修改现有Prompt、分析算法和Builder阶段实现。
- 保留`main.py`全部原有命令。
