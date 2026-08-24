from openpyxl import Workbook
from fastapi.testclient import TestClient
from quality_knowledge.mapping.repository import MappingConfigurationRepository
from quality_knowledge.mapping.service import MappingConfigurationService
from quality_knowledge.mapping.models import MappingItem
from quality_knowledge.web.app import create_app

def setup(db):
 r=MappingConfigurationRepository(db)
 c=r.create_draft('PLC',mappings=[
 MappingItem('m1','business_issue_id',['ITR单号'],[],'ISSUE_FACT','business_issue_id',True,True,'',0),
 MappingItem('m2','summary',['问题描述'],[],'ISSUE_FACT','summary',False,True,'',1),
 MappingItem('m3','site_type',['客户现场设备类型'],[],'PRODUCT_EXTENSION','customer_site_device_type',False,True,'',2)])
 r.save_validation_results(c['config_id'],[{'level':'VALID','code':'OK','message':'ok'}]); return r.activate(c['config_id'])
def excel(path):
 w=Workbook(); s=w.active; s.title='Issues'; s.append(['ITR 单号','问题描述','客户现场设备类型','新增字段']); s.append(['1','x','PLC','v']); w.save(path)
def test_preview_no_knowledge_write(tmp_path):
 db=tmp_path/'k.db'; setup(db); x=tmp_path/'x.xlsx'; excel(x)
 svc=MappingConfigurationService(MappingConfigurationRepository(db)); d=svc.preview_file(x,'PLC')
 assert d['total_source_fields']==4 and d['matched_structured']==2 and d['matched_extension']==1 and d['unmatched']==1
 assert d['coverage_percent']==75.0 and d['required_missing']==0
 import sqlite3
 with sqlite3.connect(db) as c:
  tables={x[0] for x in c.execute("select name from sqlite_master where type='table'")}
  assert 'quality_issue' not in tables or c.execute('select count(*) from quality_issue').fetchone()[0]==0
def test_web_preview_and_create_mapping(tmp_path):
 db=tmp_path/'k.db'; active=setup(db); x=tmp_path/'x.xlsx'; excel(x); client=TestClient(create_app(db))
 with open(x,'rb') as f: r=client.post('/settings/mapping/preview',files={'file':('x.xlsx',f,'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')},data={'business_type':'PLC'})
 assert r.status_code==200 and '75.0%' in r.text and '新增字段' in r.text
 q=client.post('/settings/mapping/preview/add',data={'business_type':'PLC','source_header':'新增字段','target_domain':'PRODUCT_EXTENSION','target_field':'new_field'},follow_redirects=False)
 assert q.status_code==303
 repo=MappingConfigurationRepository(db); drafts=[x for x in repo.list_configs('PLC') if x['status']=='DRAFT']
 assert drafts and any(m['target_field']=='new_field' for m in repo.get_config(drafts[0]['config_id'])['mappings'])
