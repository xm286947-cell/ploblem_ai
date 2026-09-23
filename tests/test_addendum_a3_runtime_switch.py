from pathlib import Path
from openpyxl import Workbook
from quality_knowledge.repositories import IssueKnowledgeRepository
from quality_knowledge.services import KnowledgeIssueService
from quality_knowledge.mapping.repository import MappingConfigurationRepository
from quality_knowledge.mapping.runtime import MappingNotInitializedError

def book(p):
    w=Workbook();s=w.active;s.append(['ITR单号','问题描述','问题原因定位']);s.append(['A3-1','异常','根因']);w.save(p)

def test_a3_uses_active_db_and_records_mapping_version(tmp_path):
    db=tmp_path/'q.db';f=tmp_path/'p.xlsx';book(f)
    repo=IssueKnowledgeRepository(db);svc=KnowledgeIssueService(repo);active=MappingConfigurationRepository(db).get_effective_config('PLC')
    out=svc.import_file(f,'PLC');assert out['new']==1
    with repo.connect() as c:r=c.execute('select mapping_config_id,mapping_config_version from quality_issue_version').fetchone()
    assert r['mapping_config_id']==active['config_id'] and r['mapping_config_version']==active['version']

def test_a3_no_yaml_fallback_when_active_missing(tmp_path):
    db=tmp_path/'q.db';f=tmp_path/'p.xlsx';book(f)
    repo=IssueKnowledgeRepository(db);mr=MappingConfigurationRepository(db);a=mr.get_effective_config('PLC');mr.deactivate(a['config_id'])
    svc=KnowledgeIssueService(repo)
    try: svc.import_file(f,'PLC')
    except MappingNotInitializedError as e: assert 'MAPPING_NOT_INITIALIZED' in str(e)
    else: raise AssertionError('expected MAPPING_NOT_INITIALIZED')
