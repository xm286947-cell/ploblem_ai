# Upgrade Guide

将补丁包内容覆盖到 V2.1 Sprint-1 Patch-01 工程根目录。

运行 M8.4 后检查：

```text
knowledge/repeat_analysis/<query_id>/repeat_analysis.json
knowledge/repeat_analysis/<query_id>/report.json
knowledge/repeat_analysis/<query_id>/report.md
```

执行测试：

```bash
pytest -q
```
