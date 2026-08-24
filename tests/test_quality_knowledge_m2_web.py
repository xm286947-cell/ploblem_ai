from openpyxl import Workbook
from fastapi.testclient import TestClient
from quality_knowledge.web import create_app
def make_plc(path):
    wb=Workbook();ws=wb.active;ws.title='Data';ws.append(['说明']);ws.append(['ITR单号','问题描述','问题原因定位','是否漏测','问题解决方案','平台']);ws.append(['ITR-001','PLC问题A','原因A','是','措施A','P1']);wb.save(path)
def test_web_import_list_detail_api(tmp_path):
    c=TestClient(create_app(tmp_path/'q.db'));x=tmp_path/'p.xlsx';make_plc(x)
    with x.open('rb') as f:r=c.post('/api/issues/import',files={'file':('p.xlsx',f,'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})
    assert r.status_code==200 and r.json()['new']==1
    r=c.get('/api/issues?business_type=PLC');assert r.json()['count']==1;kid=r.json()['items'][0]['knowledge_id']
    d=c.get('/api/issues/'+kid);assert d.status_code==200 and d.json()['history'][0]['version_no']==1
    assert c.get('/issues').status_code==200 and c.get('/issues/'+kid).status_code==200
def test_web_incremental_import_result(tmp_path):
    c=TestClient(create_app(tmp_path/'q.db'));x=tmp_path/'p.xlsx';make_plc(x)
    for expected in ['new','skipped']:
        with x.open('rb') as f:r=c.post('/api/issues/import',files={'file':('p.xlsx',f,'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})
        assert r.json()[expected]==1;bid=r.json()['batch_id'];assert c.get('/api/imports/'+bid).json()['batch']['status']=='COMPLETED'
