from pathlib import Path
from quality_knowledge.repositories import IssueKnowledgeRepository
from quality_knowledge.services import KnowledgeIssueService

def test_m4_statistics_empty(tmp_path):
    svc=KnowledgeIssueService(IssueKnowledgeRepository(tmp_path/'q.db'))
    s=svc.get_statistics()
    assert 'top_occurrence_causes' in s and 'cross_product_common_gaps' in s

def test_m4_exports(tmp_path):
    svc=KnowledgeIssueService(IssueKnowledgeRepository(tmp_path/'q.db'))
    x=svc.export_issues(tmp_path/'out.xlsx',format='xlsx')
    c=svc.export_issues(tmp_path/'out.csv',format='csv')
    assert Path(x['path']).exists() and Path(c['path']).exists()

def test_m4_web_routes(tmp_path):
    from fastapi.testclient import TestClient
    from quality_knowledge.web import create_app
    c=TestClient(create_app(tmp_path/'q.db'))
    assert c.get('/statistics').status_code==200
    assert c.get('/export').status_code==200
    assert c.get('/api/statistics').status_code==200
