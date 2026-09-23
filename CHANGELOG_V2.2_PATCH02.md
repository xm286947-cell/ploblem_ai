# REPEAT_CASE_ENGINE V2.2 Patch-02

## CLI运行控制

新增一键入口参数：

- `run.bat --force` / `run.bat -f`：覆盖已有中间结果并强制重跑。
- `run.bat --stage <name>`：只执行指定阶段。
- `run.bat --case <QUERY_ID>`：只处理指定Query，等价于`--query-id`。
- `run.bat --resume`：显式使用默认增量续跑模式。
- `run.bat --debug`：打印本次运行参数。

支持阶段：

- `all`
- `query`
- `retrieval`
- `candidate`
- `similarity`
- `solution`
- `repeat`

`run.bat`现已完整透传命令行参数给`analyze.py`，并保留Python退出码。
