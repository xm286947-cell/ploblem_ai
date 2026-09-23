from __future__ import annotations
import json
from pathlib import Path

from quality_knowledge.model_config import validate_quality_issue_ai_config
from quality_knowledge.repositories import IssueKnowledgeRepository
from quality_knowledge.services import KnowledgeIssueService


def test_rc2_model_yaml_is_consumed(monkeypatch):
    root=Path(__file__).resolve().parents[1]
    monkeypatch.setenv('acca','dummy-key')
    d=validate_quality_issue_ai_config(root)
    assert d['ok'] is True
    assert d['enabled'] is True
    assert d['base_url']=='http://127.0.0.1:8000/v1'
    assert d['model']=='dtcoder'
    assert d['api_key_env']=='acca'
    assert d['api_key_present'] is True
    assert d['config_path'].endswith('config/model.yaml')


def test_rc2_configurable_plc_fields_are_stored(tmp_path):
    root=Path(__file__).resolve().parents[1]
    source=Path('/mnt/data/plc_real_header_smoke.xlsx')
    if not source.exists():
        return
    repo=IssueKnowledgeRepository(tmp_path/'q.db')
    result=KnowledgeIssueService(repo).import_file(source,'PLC')
    assert result['total'] >= 1
    items=repo.query_current_issues({'business_type':'PLC'},10)
    assert items
    detail=repo.get_issue_detail(items[0]['knowledge_id'])
    normalized=json.loads(detail['issue']['normalized_json'])
    ext=normalized['product_extension']
    assert 'configured_fields' in ext
    # real smoke workbook contains ITR/问题描述 and multiple configured PLC columns
    assert len(ext['configured_fields']) >= 3


def test_rc2_ai_results_survive_repository_restart(tmp_path):
    db=tmp_path/'q.db'
    repo=IssueKnowledgeRepository(db)
    # minimal current issue/version to satisfy latest-analysis Current Version query
    with repo.connect() as c:
        c.execute("INSERT INTO quality_issue(knowledge_id,business_type,business_issue_id,current_version_id) VALUES('QK-T','PLC','ITR-T','QK-T-V1')")
        c.execute("INSERT INTO quality_issue_version(issue_version_id,knowledge_id,version_no,normalized_source_hash,normalized_json) VALUES('QK-T-V1','QK-T',1,'h','{}')")
    run={
        'analysis_run_id':'QAR-T','knowledge_id':'QK-T','issue_version_id':'QK-T-V1',
        'analysis_type':'occurrence','model_provider':'openai_compatible','prompt_name':'occurrence.md',
        'prompt_version':'p1','schema_version':'1','engine_version':'RC2','status':'RUNNING','input_hash':'x'
    }
    repo.start_analysis_run(run)
    repo.save_analysis_result('QK-T','QK-T-V1','QAR-T','occurrence',{'root_cause_summary':{'value':'原因A'}})
    repo.finish_analysis_run('QAR-T','COMPLETED',model_name='dtcoder')
    # new repository instance = process restart semantics
    repo2=IssueKnowledgeRepository(db)
    latest=repo2.get_latest_analysis('QK-T','occurrence')
    assert latest is not None
    assert latest['result']['root_cause_summary']['value']=='原因A'
    assert latest['model_name']=='dtcoder'
    assert latest['status']=='COMPLETED'
