# P0 → P1 增量补丁

本补丁以 `KNOWLEDGE_QUALITY_ISSUE_ANALYSIS_ENGINE_V1.1_QUALITY_CAPABILITY_P0_RC1_FULL.zip` 为唯一基线，只包含 P1 新增或修改文件。

覆盖补丁后必须使用全新数据库运行：

```bash
python main.py knowledge-p0-init --db ./quality_p1.db
python main.py knowledge-p0-web --db ./quality_p1.db --host 127.0.0.1 --port 8080
```

数据库 Schema 为 `2.1.0`，P0 AI 输出契约仍为 `2.0.0`。

## 变化文件

- `P1_RELEASE_NOTES.md`
- `P0_TO_P1_PATCH_MANIFEST.md`
- `quality_knowledge/config/p0_seed_manifest.json`
- `quality_knowledge/p0/initializer.py`
- `quality_knowledge/p0/schema.sql`
- `quality_knowledge/p1/__init__.py`
- `quality_knowledge/p1/risk_service.py`
- `quality_knowledge/web/api_v2.py`
- `quality_knowledge/web/p0_app.py`
- `quality_knowledge/web/p1_pages.py`
- `quality_knowledge/web/static/p1_risk_assessment.css`
- `quality_knowledge/web/static/p1_risk_assessment.js`
- `quality_knowledge/web/templates/p0_console.html`
- `quality_knowledge/web/templates/p0_insights.html`
- `quality_knowledge/web/templates/p0_issues.html`
- `quality_knowledge/web/templates/p1_risk_assessment.html`
- `tests/test_forward_risk_p1a.py`
- `tests/test_forward_risk_p1a_ued.py`
