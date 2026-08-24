# QUALITY KNOWLEDGE M3 DELIVERY

Version: REPEAT_CASE_ENGINE_V2.4_QUALITY_KNOWLEDGE_M3  
Scope: Query & Export  
Status: Released

## 1. Implemented

- Cross-business combined query for HMI / PLC / IFA.
- Filters for occurrence / escape / product / platform / month / severity / recurrence risk / capability gap.
- Capability gap query by TECHNICAL / MANAGEMENT / GOVERNANCE and category.
- Aggregations:
  - TOP occurrence cause
  - TOP escape cause
  - TOP capability gap
  - recurrence risk distribution
  - cross-business common capability gaps
  - issue counts by business
- CSV export for Issue Knowledge or Capability Gaps.
- XLSX business export with sheets:
  - Issue_Knowledge
  - Capability_Gaps
  - Analysis_Runs
  - Statistics
- New CLI commands:
  - `stats-quality-knowledge`
  - `export-quality-knowledge`
- Existing `query-quality-knowledge` extended without changing previous arguments.

## 2. Compatibility

- Existing Repeat Case similarity / solution analysis / repeat decision logic unchanged.
- M1 import contract unchanged.
- M2 four-stage analysis contract unchanged.
- SQLite remains repository implementation, not upper-layer contract.

## 3. Test Result

- Quality Knowledge M1/M2/M3 targeted tests: 8 passed.
- Full regression: 137 passed, 0 failed.
- CLI smoke verified:
  - import
  - combined query
  - statistics
  - XLSX export
  - CSV export

## 4. Key Commands

```bash
python main.py query-quality-knowledge \
  --db knowledge/quality_issue.db \
  --business-type PLC \
  --is-escape 是 \
  --gap-dimension MANAGEMENT \
  --gap-category CHANGE_MANAGEMENT

python main.py stats-quality-knowledge \
  --db knowledge/quality_issue.db \
  --business-type PLC

python main.py export-quality-knowledge \
  --db knowledge/quality_issue.db \
  --output output/quality_knowledge.xlsx \
  --format xlsx

python main.py export-quality-knowledge \
  --db knowledge/quality_issue.db \
  --output output/capability_gaps.csv \
  --format csv \
  --dataset capability_gaps \
  --gap-dimension MANAGEMENT
```

## 5. Main Modified / Added Files

- `quality_knowledge/repositories/sqlite_repository.py`
- `quality_knowledge/services/query_service.py`
- `quality_knowledge/services/export_service.py`
- `quality_knowledge/services/__init__.py`
- `main.py`
- `tests/test_quality_knowledge_m3.py`
- `QUALITY_KNOWLEDGE_M3_DELIVERY.md`
