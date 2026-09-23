# KNOWLEDGE_QUALITY_ISSUE_ANALYSIS_ENGINE V1.0 M2
Status: Released
Design: KNOWLEDGE_QUALITY_ISSUE_ANALYSIS_DESIGN_V1.0 Frozen

## Scope
M2 Web Foundation: Web Backend, Import, Issues, Issue Detail, Query/Filter, Import Result.

## Run
pip install -r requirements.txt
python main.py knowledge-web --db knowledge/quality_issue_v1.db --host 127.0.0.1 --port 8080
Open: http://127.0.0.1:8080

## Web
- /import: Excel upload + AUTO/HMI/PLC/IFA
- /imports/{batch_id}: total/new/updated/skipped/failed + diagnostics/errors
- /issues: current-version list + filters
- /issues/{knowledge_id}: current normalized/original/source/version history

## API
- POST /api/issues/import
- GET /api/imports/{batch_id}
- GET /api/issues
- GET /api/issues/{knowledge_id}
- GET /api/issues/{knowledge_id}/versions

Web/API and CLI share KnowledgeIssueService and IssueKnowledgeRepository. Controllers do not execute SQL.

## Tests
M2 Web: 2 passed
Full regression: 144 passed, 0 failed
