from pathlib import Path
from quality_knowledge.mapping import MappingConfigurationRepository
from quality_knowledge.mapping.migration import LegacyYamlMappingMigrator
ROOT=Path(__file__).resolve().parents[1]

def test_dry_run_all(tmp_path):
    r=MappingConfigurationRepository(tmp_path/'a.db'); m=LegacyYamlMappingMigrator(r)
    for bt in ('HMI','PLC','IFA'):
        out=m.migrate(ROOT/'quality_knowledge'/'config'/f'{bt.lower()}_fields.yaml',bt,True)
        assert out['business_type']==bt and out['canonical_field_count']>0 and out['invalid_count']==0
        assert r.list_configs(bt)==[]

def test_apply_first_activates_and_is_idempotent(tmp_path):
    r=MappingConfigurationRepository(tmp_path/'a.db'); m=LegacyYamlMappingMigrator(r); p=ROOT/'quality_knowledge/config/plc_fields.yaml'
    a=m.migrate(p,'PLC',False); assert a['migration_result']=='APPLIED' and a['status']=='ACTIVE'
    b=m.migrate(p,'PLC',False); assert b['migration_result']=='SKIPPED'
    assert len(r.list_configs('PLC'))==1

def test_changed_yaml_creates_draft_not_silent_active(tmp_path):
    r=MappingConfigurationRepository(tmp_path/'a.db'); m=LegacyYamlMappingMigrator(r); src=ROOT/'quality_knowledge/config/hmi_fields.yaml'
    m.migrate(src,'HMI',False)
    p=tmp_path/'hmi_fields.yaml'; p.write_text(src.read_text(encoding='utf-8')+'\n# changed\n',encoding='utf-8')
    b=m.migrate(p,'HMI',False); assert b['version']==2 and b['status']=='DRAFT' and not b['activated']
    assert r.get_effective_config('HMI')['version']==1

def test_duplicate_legacy_aliases_are_deduplicated_before_insert(tmp_path):
    p=tmp_path/'hmi.yaml'
    p.write_text('''business_type: HMI
identity:
  business_issue_id:
    required: true
    aliases: [TRC单号, TRC单号, " TRC单号 "]
''',encoding='utf-8')
    r=MappingConfigurationRepository(tmp_path/'mapping.db')
    out=LegacyYamlMappingMigrator(r).migrate(p,'HMI',False)
    assert out['migration_result']=='APPLIED'
    item=r.get_effective_config('HMI')['mappings'][0]
    assert item['source_headers']==['TRC单号'] and item['aliases']==['TRC单号']
