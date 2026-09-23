import json, sqlite3
from pathlib import Path
from openpyxl import Workbook
from builder.ai_client import AIResponse
from quality_knowledge.repositories import IssueKnowledgeRepository
from quality_knowledge.services import KnowledgeIssueService
from quality_knowledge.services.v1_analysis_service import KnowledgeIssueAnalysisService
from quality_knowledge.model_config import load_quality_issue_ai_config, list_quality_issue_agents,choose_quality_issue_agent

ROOT=Path(__file__).parents[1]

class FixedClient:
    def __init__(self): self.calls=[]
    def complete(self,messages):
        self.calls.append(messages)
        sys=messages[0]['content']
        ref={'source_type':'FIELD','source_id':'PLC:ITR-1','field_path':'issue_fact.description'}
        ev={'value':'结论','confidence':.9,'evidence_type':'INFERRED','reason':'证据','source_refs':[ref]}
        if '发生原因分析器' in sys:
            d={'root_cause_summary':ev,'failure_mechanism':ev,'contributing_factors':[ev],'occurrence_category':'变更引入','confidence':.9,'evidence':[ref]}
        elif '流出原因分析器' in sys:
            d={'escape_cause_summary':ev,'verification_gap':ev,'process_gap':ev,'escape_category':'测试遗漏','confidence':.9,'evidence':[ref]}
        elif '再发风险分析器' in sys:
            d={'recurrence_risk_level':'HIGH','recurrence_risk_reason':ev,'existing_control_coverage':ev,'residual_risk':ev,'is_common_issue':True,'potential_affected_products':['HMI'],'horizontal_action_needed':True}
        else:
            # deliberately repeat LLM gap_id to verify the DB never trusts it.
            d={'capability_gaps':[{'gap_id':'GAP-001','dimension':'TECHNICAL','category':'TEST_CAPABILITY','description':'测试能力不足','why_needed':'防再发','related_mechanism':'遗漏','recommended_control':'增加测试','recommended_action':'建立专项测试','action_type':'TECHNICAL_BUILD','action_target':'测试资产','expected_prevention_effect':'降低再发','scope':'产品','affected_products':['PLC'],'confidence':.9,'evidence':[ref]}]}
        return AIResponse(json.dumps(d,ensure_ascii=False),'fake-qwen',{})

def seed(tmp_path):
    f=tmp_path/'plc.xlsx'
    w=Workbook();s=w.active
    s.append(['ITR单号','问题描述','问题原因定位','是否漏测','问题解决方案'])
    s.append(['ITR-1','异常'*100,'变更引入','是','增加测试'])
    w.save(f)
    repo=IssueKnowledgeRepository(tmp_path/'q.db');svc=KnowledgeIssueService(repo)
    svc.import_file(f,'PLC')
    kid=svc.query_issues({},10)[0]['knowledge_id']
    return repo,svc,kid

def test_completed_is_skipped_by_default_and_force_reanalyzes(tmp_path):
    repo,svc,kid=seed(tmp_path);client=FixedClient()
    first=svc.run_issue_analysis(kid,ROOT,client=client)
    assert first['status']=='COMPLETED'
    assert len(repo.get_analysis_history(kid))==4
    second=svc.run_issue_analysis(kid,ROOT,client=client)
    assert all(x['status']=='SKIPPED_COMPLETED' for x in second['stages'].values())
    assert len(repo.get_analysis_history(kid))==4
    forced=svc.run_issue_analysis(kid,ROOT,client=client,force=True)
    assert forced['status']=='COMPLETED'
    assert len(repo.get_analysis_history(kid))==8

def test_only_missing_batch_excludes_fully_completed_issues(tmp_path):
    repo,svc,kid=seed(tmp_path);client=FixedClient()
    svc.run_issue_analysis(kid,ROOT,client=client)
    history_count=len(repo.get_analysis_history(kid))
    result=svc.run_batch_analysis([kid],ROOT,client=client,only_missing=True,concurrency=2)
    assert result['requested_total']==1
    assert result['total']==0
    assert result['skipped_completed']==1
    assert result['items']==[]
    assert len(repo.get_analysis_history(kid))==history_count

def test_repeated_llm_gap_ids_do_not_collide(tmp_path):
    repo,svc,kid=seed(tmp_path);client=FixedClient()
    svc.run_issue_analysis(kid,ROOT,client=client)
    svc.run_issue_analysis(kid,ROOT,client=client,force=True)
    gaps=repo.list_capability_gaps(kid)
    ids=[x['gap_id'] for x in gaps]
    assert len(ids)>=2 and len(ids)==len(set(ids))
    assert 'GAP-001' not in ids

def test_stage_runtime_and_stale_running_recovery(tmp_path):
    repo,svc,kid=seed(tmp_path)
    with repo.connect() as c:
        c.execute("""INSERT INTO analysis_run(analysis_run_id,knowledge_id,issue_version_id,analysis_type,status,started_at)
                     VALUES(?,?,?,?,?,'2020-01-01 00:00:00')""",
                  ('STALE-1',kid,repo.get_current_issue(kid)['current_version_id'],'recurrence','RUNNING'))
    analysis=KnowledgeIssueAnalysisService(repo,ROOT,client=FixedClient())
    assert analysis.stage_timeouts['recurrence']==240
    assert analysis.stage_timeouts['capability_gap']==300
    run=repo.get_analysis_run('STALE-1')
    assert run['status']=='FAILED'
    assert 'STALE_RUNNING_TIMEOUT' in run['error_message']

def test_recurrence_and_capability_context_is_slimmed(tmp_path):
    repo,svc,kid=seed(tmp_path);client=FixedClient()
    svc.run_issue_analysis(kid,ROOT,client=client,force=True)
    user_payloads=[json.loads(call[1]['content']) for call in client.calls]
    recurrence=user_payloads[2];capability=user_payloads[3]
    assert 'occurrence_analysis' in recurrence and 'escape_analysis' in recurrence
    assert 'recurrence_analysis' in capability
    # Heavy raw/debug source must not be carried forward.
    assert 'raw_json' not in json.dumps(recurrence,ensure_ascii=False)
    assert 'raw_json' not in json.dumps(capability,ensure_ascii=False)

def test_analysis_profile_is_injected_and_audited(tmp_path):
    repo,svc,kid=seed(tmp_path);client=FixedClient()
    profile={'domain_profile':'EMBEDDED','issue_types':['COMPATIBILITY','VERSION_COMBINATION'],'lifecycle_phase':'SOLUTION_INTEGRATION'}
    out=svc.run_issue_analysis(kid,ROOT,client=client,analysis_profile=profile)
    assert out['analysis_profile']['domain_profile']=='EMBEDDED'
    payload=json.loads(client.calls[0][1]['content'])
    assert payload['analysis_profile']['issue_types']==['COMPATIBILITY','VERSION_COMBINATION']
    run=repo.get_analysis_run(out['stages']['occurrence']['run_id'])
    stored=json.loads(run['analysis_profile_json'])
    assert stored['lifecycle_phase']=='SOLUTION_INTEGRATION'
    # A changed user selection invalidates reuse for the current version.
    rerun=svc.run_issue_analysis(kid,ROOT,client=client,analysis_profile={'domain_profile':'MECHANICAL'})
    assert rerun['stages']['occurrence']['status']=='COMPLETED'

def test_multiple_agents_support_explicit_and_dynamic_stage_routing():
    agents=list_quality_issue_agents(ROOT)
    assert {'quality','fast'} <= {x['agent_id'] for x in agents['items']}
    explicit,_=load_quality_issue_ai_config(ROOT,agent_id='fast',stage='occurrence')
    assert explicit['_agent_id']=='fast'
    assert choose_quality_issue_agent(ROOT,'K-1') in {'quality','fast'}

def test_selected_agent_is_audited_per_analysis_run(tmp_path):
    repo,svc,kid=seed(tmp_path)
    out=svc.run_issue_analysis(kid,ROOT,client=FixedClient(),agent_id='fast')
    run=repo.get_analysis_run(out['stages']['occurrence']['run_id'])
    assert json.loads(run['analysis_profile_json'])['analysis_agent']=='fast'
    assert repo.get_analysis_history(kid)[0]['analysis_agent']=='fast'

def test_dynamic_assignment_uses_one_agent_per_issue_and_round_robins_batch(tmp_path):
    config=tmp_path/'config';config.mkdir()
    (config/'model.yaml').write_text('''
ai: {enabled: true, provider: openai_compatible, base_url: http://localhost/v1, api_key_env: TEST_KEY, model: base}
quality_issue_agents:
  deep: {label: 深度, model: model-deep}
  fast: {label: 快速, model: model-fast}
''',encoding='utf-8')
    summary=list_quality_issue_agents(tmp_path)
    assert choose_quality_issue_agent(tmp_path,'K1',slot=0)=='deep'
    assert choose_quality_issue_agent(tmp_path,'K2',slot=1)=='fast'
    assert choose_quality_issue_agent(tmp_path,'K3',slot=2)=='deep'
    assert summary['distinct_model_count']==2
    assert summary['assignment_strategy']=='ROUND_ROBIN_BY_ISSUE'
