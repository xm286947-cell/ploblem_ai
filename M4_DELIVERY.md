# REPEAT_CASE_ENGINE V2.3 M4 RC1 Delivery

## 目标

完成新问题分析链路的端到端编排，不修改既有业务算法。

## 本次完成

- 新增 `builder/analysis_pipeline_runner.py`。
- 新增 CLI：`run-analysis`。
- 串联 M7.1、M7.2、M7.3、M8.1、M8.2、M8.3、M8.4。
- 支持单 Query 和批量 Query。
- 支持 `--from-stage` 断点续跑。
- 支持单 Query 失败隔离，其他 Query 继续执行。
- 统一记录阶段状态、耗时、失败阶段和错误原因。
- 运行摘要输出至 `output/logs/analysis_pipeline_summary.json`。

## CLI 示例

```bash
python main.py run-analysis --input input/new_cases.xlsx --mock --overwrite
python main.py run-analysis --query-id QUERY001 --from-stage similarity --mock --overwrite
python main.py run-analysis --query-id QUERY001 --from-stage decision --skip-ai --overwrite
```

## 状态定义

- `SUCCESS`：全部 Query 成功。
- `PARTIAL_SUCCESS`：部分 Query 成功、部分失败。
- `FAILED`：解析失败、无可执行 Query，或全部 Query 失败。

## 验证结果

- 全量自动化测试：`103 passed`。
- 实际 CLI 编排验证：能够正确识别 M6 索引缺失，并停止当前 Query 后续阶段；失败原因写入 Pipeline Summary。

## 已知前置条件

检索阶段依赖 M6 索引：

`knowledge/index/case_index.jsonl`

索引不存在时，Pipeline 将在 `retrieve` 阶段明确失败，不会继续生成伪分析结果。
