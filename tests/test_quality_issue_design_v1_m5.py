import json, subprocess, sys
from pathlib import Path
from openpyxl import Workbook
from fastapi.testclient import TestClient
from quality_knowledge.repositories import IssueKnowledgeRepository
from quality_knowledge.services import KnowledgeIssueService
from quality_knowledge.web import create_app

ROOT=Path(__file__).parents[1]

def make_book(path, desc='异常'):
    w=Workbook();s=w.active;s.title='Data'
    s.append(['质量问题清单']);s.append(['说明'])
    s.append(['ITR单号','问题描述','问题原因定位','是否漏测','原因分类一级','产品'])
    s.append(['ITR-M5-1',desc,'变更引入','是','测试','PLC'])
    w.save(path)

def test_m5_web_cli_service_consistency_and_versioning(tmp_path):
    db=tmp_path/'q.db'; f=tmp_path/'plc.xlsx';make_book(f)
    svc=KnowledgeIssueService(IssueKnowledgeRepository(db)); r=svc.import_file(f)
    assert r['new']==1 and r['failed']==0
    service_rows=svc.query_issues({'business_type':'PLC'},100)
    app=create_app(db); c=TestClient(app); api=c.get('/api/issues?business_type=PLC').json()
    api_rows=api.get('items',api) if isinstance(api,dict) else api
    assert len(service_rows)==len(api_rows)==1
    kid=service_rows[0]['knowledge_id']; assert len(svc.get_issue_history(kid))==1
    make_book(f,'异常已更新'); r2=svc.import_file(f)
    assert r2['updated']==1 and len(svc.get_issue_history(kid))==2
    r3=svc.import_file(f); assert r3['skipped']==1

def test_m5_cli_current_query_matches_service(tmp_path):
    db=tmp_path/'q.db';f=tmp_path/'plc.xlsx';make_book(f)
    svc=KnowledgeIssueService(IssueKnowledgeRepository(db));svc.import_file(f)
    p=subprocess.run([sys.executable,str(ROOT/'main.py'),'knowledge-query','--db',str(db),'--business-type','PLC'],cwd=ROOT,capture_output=True,text=True,check=True)
    out=json.loads(p.stdout); assert out['count']==len(svc.query_issues({'business_type':'PLC'},100))==1

def test_m5_repeat_case_contract_untouched():
    # Frozen Design V1.0: formal Quality Issue service is additive; Repeat Case algorithms remain their existing modules.
    from builder.similarity_analyzer import SimilarityAnalyzer
    from builder.m84_repeat_runner import run_m84_decision
    assert SimilarityAnalyzer is not None and callable(run_m84_decision)
