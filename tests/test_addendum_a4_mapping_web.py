from fastapi.testclient import TestClient
from quality_knowledge.web.app import create_app
from quality_knowledge.mapping.repository import MappingConfigurationRepository
from quality_knowledge.mapping.models import MappingItem

def seed(db):
    r=MappingConfigurationRepository(db)
    c=r.create_draft('PLC',source_type='YAML_MIGRATION',source_file='plc_fields.yaml',source_hash='abc',mappings=[
        MappingItem('m1','business_issue_id',['ITR单号'],['ITR 单号'],'ISSUE_FACT','business_issue_id',True,True,'',0),
        MappingItem('m2','summary',['问题描述'],[],'ISSUE_FACT','summary',False,True,'',1)])
    r.save_validation_results(c['config_id'],[{'level':'VALID','code':'OK','message':'ok'}]); return r.activate(c['config_id'])

def test_mapping_settings_and_draft(tmp_path):
    db=tmp_path/'k.db'; cfg=seed(db); client=TestClient(create_app(db))
    x=client.get('/settings/mapping?business_type=PLC')
    assert x.status_code==200 and '源字段映射配置' in x.text and 'ITR单号' in x.text
    y=client.post(f"/settings/mapping/{cfg['config_id']}/draft",follow_redirects=False)
    assert y.status_code==303
    r=MappingConfigurationRepository(db); rows=r.list_configs('PLC')
    assert rows[0]['status']=='DRAFT' and rows[1]['status']=='ACTIVE'

def test_validate_activate_export(tmp_path):
    db=tmp_path/'k.db'; active=seed(db); client=TestClient(create_app(db))
    d=client.post(f"/settings/mapping/{active['config_id']}/draft",follow_redirects=False)
    config_id=d.headers['location'].split('config_id=')[1]
    assert client.post(f'/settings/mapping/{config_id}/validate',follow_redirects=False).status_code==303
    assert client.post(f'/settings/mapping/{config_id}/activate',follow_redirects=False).status_code==303
    assert MappingConfigurationRepository(db).get_effective_config('PLC')['config_id']==config_id
    e=client.get(f'/settings/mapping/{config_id}/export?format=json')
    assert e.status_code==200 and b'business_type' in e.content
