from pathlib import Path
from openpyxl import Workbook
from quality_knowledge.repositories import SqliteIssueKnowledgeRepository
from quality_knowledge.services import IssueImportService

def make_xlsx(path, headers, values):
    wb=Workbook(); ws=wb.active; ws.title='data'; ws.append(headers); ws.append(values); wb.save(path)

def test_hmi_import_raw_and_three_dimensions(tmp_path):
    x=tmp_path/'hmi.xlsx'; db=tmp_path/'k.db'
    make_xlsx(x,['TRC单号','问题描述','一级分类','流出原因分类','根因分析','是否漏测','自定义字段'],['T1','黑屏','代码','测试覆盖','空指针','是','必须保留'])
    r=SqliteIssueKnowledgeRepository(db); out=IssueImportService(r).import_excel(x,'HMI')
    assert out['imported']==1
    row=r.query({'business_type':'HMI'})[0]
    assert row['occurrence_l1']=='代码' and row['escape_l1']=='测试覆盖'
    import sqlite3, json
    c=sqlite3.connect(db); raw=json.loads(c.execute('select raw_json from issue_source_raw').fetchone()[0]); assert raw['自定义字段']=='必须保留'

def test_plc_and_ifa_mapping(tmp_path):
    db=tmp_path/'k.db'; r=SqliteIssueKnowledgeRepository(db)
    p=tmp_path/'plc.xlsx'; make_xlsx(p,['ITR单号','问题描述','新二级分类','原因分类一级','是否漏测'],['P1','在线修改慢','性能','用例缺失','是'])
    i=tmp_path/'ifa.xlsx'; make_xlsx(i,['ITR单号','Bug标题','问题描述','一级分类','原因一级分类','是否漏测'],['I1','碰撞异常','仿真错','功能','场景覆盖','是'])
    IssueImportService(r).import_excel(p,'PLC'); IssueImportService(r).import_excel(i,'IFA')
    assert r.query({'business_type':'PLC'})[0]['occurrence_l2']=='性能'
    assert r.query({'business_type':'IFA'})[0]['escape_l1']=='场景覆盖'

def test_idempotent_same_source(tmp_path):
    x=tmp_path/'hmi.xlsx'; db=tmp_path/'k.db'; make_xlsx(x,['TRC单号','问题描述'],['T1','x'])
    r=SqliteIssueKnowledgeRepository(db); svc=IssueImportService(r)
    assert svc.import_excel(x,'HMI')['imported']==1
    assert svc.import_excel(x,'HMI')['skipped']==1
