# REPEAT_CASE_ENGINE V2.4 M6 Local Batch Delivery

## 定位

面向敏感真实数据的本地批量运行、断点续跑、失败诊断与运行追溯。真实数据不需要上传。

## 新增命令

```bash
python main.py run-batch --input input/new_cases.xlsx
```

续跑指定批次：

```bash
python main.py run-batch --run-id RUN_20260728_090000 --resume
```

仅重跑失败 Query：

```bash
python main.py run-batch --run-id RUN_20260728_090000 --resume --retry-failed
```

## 输出结构

```text
output/runs/<RUN_ID>/
├── run_manifest.json
├── parse_result.json
├── summary.json
├── summary.md
├── failed_queries.json
└── queries/
    └── <QUERY_ID>/
        ├── status.json
        ├── pipeline_result.json
        ├── traceback.txt              # 仅异常时
        └── artifacts/
            ├── report.json
            ├── report.md
            └── repeat_analysis.json
```

默认不复制 raw_query、standard_query 等敏感中间数据。只有显式增加 `--include-sensitive-debug` 才会复制到 Run 目录。

## 核心能力

1. 每个 Query 执行前自动清理其历史中间产物。
2. Query 逐个隔离运行，单个失败不阻断整批任务。
3. 每个 Query 独立保存状态、耗时、失败阶段和错误。
4. 支持 Run Manifest、失败清单、批次汇总和断点续跑。
5. 默认遵循真实敏感数据本地保存原则。
