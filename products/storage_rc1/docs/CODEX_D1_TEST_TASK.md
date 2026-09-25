# Codex Test Task — RC3-D1 四器件抽取链路收敛

角色：独立测试方，只测试/审视，不重构已冻结路线。

## 目标

验证本开发包是否在原 RC3 主工程内完成以下收敛，且没有破坏已有产品能力：

1. 正常正式规格事实抽取默认 1 次模型调用；
2. NAND/NOR/eMMC/SSD 共用单次抽取入口；
3. Contract Adapter 不改事实值；
4. Evidence Resolver 不调用模型，只从 source_id + page 恢复原文；
5. 普通 missing 不触发 Review；异常才进入 Review Queue；
6. 新导入未命中 Gate 时不得调用 Final Review；
7. 多来源 conflict 保留双方 provenance；
8. 旧 RC3 PDF/MD、SQLite、人工确认、Reviewed Specification、Device Conclusion、UI/API 回归不破坏。

## 必跑

```bash
python3 -m py_compile storage_life/*.py
python3 -m pytest -q
```

预期：68 passed。

## 重点代码审视

- `storage_life/ai.py`
  - `extract_specification_bundle_once`
  - `extract_specification_once`
  - `_adapt_single_pass`
  - `_resolve_locator`
  - `_normalize_candidate_representation`
- `storage_life/core.py`
  - `import_document`
  - `get_extraction_run`
  - `candidate_evidence_provenance`
  - `extraction_runs`
- `storage_life/app.py`
  - `/api/devices/{device_id}/extraction-status`
  - Final Review Gate
- `storage_life/index.html`
  - 导入后不自动 Final Review
- `config/agent.yaml`
  - qwen3.8-max / Token Plan / DASHSCOPE_API_KEY

## 失败分类

- CONTRACT：输出契约/兼容性问题
- LOGIC：单次抽取、Resolver、Normalizer、Gate 逻辑问题
- REGRESSION：旧 RC3 能力回归
- TEST：测试不足或错误
- SOURCE：source_id/provenance/多来源冲突问题
- SCOPE：越界重构或第二套系统

## 输出

只输出：
- PASS / FIX
- deviations（file/line/test evidence）
- 必须修复项
- 可后置项

不要因代码风格产生 FIX，不要要求重写现有产品层。
