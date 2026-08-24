# REPEAT_CASE_ENGINE V2.3 M5 RC1 Delivery

## 1. Milestone

- Version: V2.3 M5 RC1
- Capability: Delivery Capability
- Delivery Contract: `REPEAT_CASE_REPORT` V1.0

## 2. Completed

1. 新增正式交付阶段 M8.5：`run-m85-delivery`。
2. 以 `repeat_analysis.json` 为唯一分析输入，生成正式 `Report JSON`。
3. Markdown 仅作为 Report JSON 的 Renderer 输出。
4. 正式交付目录统一为：`output/reports/<query_id>/`。
5. 新增批量报告索引：
   - `output/reports/report_index.json`
   - `output/reports/report_index.md`
6. `run-analysis` 端到端链路新增 `delivery` 阶段。
7. 支持从 `--from-stage delivery` 单独恢复交付。
8. 保留 M8.4 原有输出，避免破坏历史兼容。

## 3. Official Artifacts

每个 Query 输出：

```text
output/reports/<query_id>/report.json
output/reports/<query_id>/report.md
```

其中：

- `report.json`：唯一正式交付件 / Delivery Contract。
- `report.md`：基于 Report JSON 生成的人读报告。

批量索引：

```text
output/reports/report_index.json
output/reports/report_index.md
```

## 4. CLI

单独执行交付：

```bash
python main.py run-m85-delivery --query-id Q1 --overwrite
```

完整链路：

```bash
python main.py run-analysis --overwrite
```

从交付阶段恢复：

```bash
python main.py run-analysis --query-id Q1 --from-stage delivery --overwrite
```

## 5. Compatibility

未修改：

- Knowledge 结构
- Similarity 算法
- Solution 分析
- Repeat Decision 逻辑
- Prompt
- Schema

M8.4 原有 `knowledge/repeat_analysis/<query_id>/report.json` 与 `report.md` 仍保留；M8.5 新增正式交付目录，不影响旧调用。

## 6. Verification

```text
106 passed
```

CLI Help 已验证包含：

- `run-analysis`
- `run-m85-delivery`

## 7. Known Boundary

- HTML、Portal、API 本次仅保留扩展边界，未实现 Renderer。
- M6 仅进入 Release Validation，不新增交付模型。
