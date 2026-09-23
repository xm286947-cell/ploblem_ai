import sqlite3
import pytest
from quality_knowledge.mapping import MappingConfigurationRepository, MappingConfigurationService, MappingItem

def test_a1_persistence_and_restart(tmp_path):
    db=tmp_path/'knowledge.db'; repo=MappingConfigurationRepository(db); svc=MappingConfigurationService(repo)
    item=MappingItem('MI-1','business_issue_id',['ITR 单号'],['ITR单号'],'ISSUE_FACT','business_issue_id',True,True,'业务唯一键',1)
    draft=svc.create_draft('PLC',source_type='YAML_MIGRATION',source_file='plc_fields.yaml',source_hash='abc',mappings=[item])
    assert draft['status']=='DRAFT' and draft['mappings'][0]['aliases']==['ITR单号']
    rid=repo.save_validation_results(draft['config_id'],[{'level':'VALID','code':'OK','message':'valid'}]); assert rid
    active=svc.activate_config(draft['config_id']); assert active['status']=='ACTIVE'
    repo2=MappingConfigurationRepository(db); eff=repo2.get_effective_config('PLC')
    assert eff['config_id']==draft['config_id'] and eff['source_hash']=='abc' and repo2.schema_version()==1

def test_a1_single_active_and_versioning(tmp_path):
    repo=MappingConfigurationRepository(tmp_path/'k.db')
    a=repo.create_draft('PLC'); repo.activate(a['config_id'])
    b=repo.create_draft('PLC'); assert b['version']==2; repo.activate(b['config_id'])
    assert repo.get_config(a['config_id'])['status']=='INACTIVE'; assert repo.get_effective_config('PLC')['version']==2
    with repo.connect() as c: assert c.execute("SELECT COUNT(*) FROM mapping_config WHERE business_type='PLC' AND status='ACTIVE'").fetchone()[0]==1

def test_a1_validation_error_blocks_activation_and_audit_tables(tmp_path):
    repo=MappingConfigurationRepository(tmp_path/'k.db'); d=repo.create_draft('HMI',source_type='WEB')
    repo.save_validation_results(d['config_id'],[{'level':'ERROR','code':'REQUIRED_MISSING','message':'business_issue_id missing'}])
    with pytest.raises(ValueError): repo.activate(d['config_id'])
    run=repo.start_migration_run(business_type='HMI',source_file='hmi_fields.yaml',source_hash='hash1',dry_run=True); repo.finish_migration_run(run,'COMPLETED',{'valid':1})
    with repo.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM mapping_migration_run').fetchone()[0]==1
        assert c.execute("SELECT version FROM knowledge_schema_version WHERE component='mapping'").fetchone()[0]==1
