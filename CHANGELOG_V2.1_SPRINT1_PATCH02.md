# REPEAT_CASE_ENGINE V2.1 Sprint-1 Patch-02

## 新增

- `presentation/renderer/markdown_renderer.py`
- `presentation/renderer/__init__.py`
- MarkdownRenderer 单元测试

## 修改

- `presentation/__init__.py`：导出 MarkdownRenderer
- `builder/m84_repeat_runner.py`：在 `report.json` 生成后同步生成 `report.md`
- `tests/test_m84_repeat_decision.py`：增加 Markdown 交付件验证

## 输出

每个查询目录新增：

```text
knowledge/repeat_analysis/<query_id>/report.md
```

原有 `repeat_analysis.json` 与 `report.json` 保持不变。
