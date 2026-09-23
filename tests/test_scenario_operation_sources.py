import json
from fastapi.testclient import TestClient
from builder.ai_client import AIResponse
from quality_knowledge.web.app import create_app
from quality_knowledge.materials import MaterialRepository
from quality_knowledge.scenario_sources import operation_records, operation_scope_counts, period, normalize_problem_domain
from quality_knowledge.scenario_generation import PROMPT


def seed(tmp_path):
    app=create_app(tmp_path/'sources.db');svc=app.state.scenario_generation_service
    repo=MaterialRepository(tmp_path/'sources.db');ids=[]
    for i in range(3):
        key=f'ITR2025010000{i}CS'
        raw={'问题信息_彻底解决单号':key,'问题信息_IPMT':'FA','问题信息_SPDT':'PLC','问题信息_产品型号':'M1',
             '数据运营_KPI计入月份':'2026-08','问题信息_问题描述':'考核摘要'}
        mid,_=repo.add_material(repo.group('SW-OPS'),key,raw,'synthetic.xlsx','test',i+1);ids.append(mid)
        repo.add_material(repo.group('ITR-CS'),key,{'问题信息_问题描述':'连续运行72小时后，资源累积导致延迟',
            '问题信息_问题原因定位':'原始根因','技术根因分析与纠正_TRC纠正信息':'修复资源释放',
            '问题信息_客户行业':'测试行业','问题信息_客户名称':'测试客户','问题信息_产品型号':'M1'},'synthetic.xlsx','test',i+1)
        if i<2:
            with repo.connect() as c:c.execute('INSERT INTO quality_issue(knowledge_id,business_type,business_issue_id) VALUES(?,?,?)',(f'K{i}','PLC',key))
    class Issues:
        def get_latest_analysis(self,kid,stage):
            if stage=='occurrence':return {'result':{'root_cause_summary':{'value':'漏测分析确认根因'}}}
            if stage=='escape' and kid=='K0':return {'result':{'escape_cause_summary':'未覆盖长时间运行'}}
            return None
    svc.issues=Issues()
    return app,svc,repo,ids


def test_source_priority_filters_and_unknown_year(tmp_path):
    app,svc,repo,ids=seed(tmp_path)
    rows=operation_records(svc,{'ipmt':'FA','spdt':'PLC','product_model':'M1','year':'2026','start_month':'8','end_month':'8','product_code':'PLC'})
    assert len(rows)==3
    assert {r['source_status'] for r in rows}=={'LEAKAGE','PARTIAL','CS_ONLY'}
    assert all(r['description'].startswith('连续运行') for r in rows)
    assert all(r['occurrence']['root_cause']=='漏测分析确认根因' for r in rows if r['linked_knowledge_id'])
    assert next(r for r in rows if not r['linked_knowledge_id'])['occurrence']['root_cause']=='原始根因'
    assert not operation_records(svc,{'ipmt':'其他'})
    assert period({'数据运营_KPI计入月份':'8月','问题信息_彻底解决单号':'ITR202501001CS'})==('未知','8')


def test_problem_domain_uses_structured_evidence_only():
    assert normalize_problem_domain('软件')=='SOFTWARE'
    assert normalize_problem_domain('硬件')=='HARDWARE'
    assert normalize_problem_domain('机械')=='MECHANICAL'
    assert normalize_problem_domain('',{'software_module':'Runtime'})=='SOFTWARE'
    assert normalize_problem_domain('',{'component_code':'R100'})=='HARDWARE'
    assert normalize_problem_domain('',{'mechanical_failure_mode':'壳体开裂'})=='MECHANICAL'
    assert normalize_problem_domain('')=='UNKNOWN'
    assert normalize_problem_domain('',software_operation=True)=='SOFTWARE'


def test_http_selection_snapshot_generation_and_material_links(tmp_path):
    app,svc,repo,ids=seed(tmp_path);client=TestClient(app)
    class Client:
        def complete(self,messages):
            row=json.loads(messages[1]['content'])['records'][0]
            assert '阶段判定必须比较' in messages[0]['content']
            return AIResponse(json.dumps({'items':[{'name':'持续运行响应及时性','lifecycle_code':'LONG_TERM_OPERATION','activity_code':'RESOURCE_STATE_RETENTION',
                'evidence_issue_ids':[row['knowledge_id']],'lifecycle_reason':'持续72小时资源累积是必要条件',
                'lifecycle_assessment':{'RUNTIME_EXECUTION':'单次执行无异常','SYSTEM_INTEGRATION':'无跨系统必要条件','LONG_TERM_OPERATION':'72小时持续运行触发'},'confidence':0.8}]}),'test-model',{})
    svc.ai_client=Client()
    # Run the real job with a deterministic client, without external model calls.
    rows=operation_records(svc,{'product_code':'PLC'})
    svc.save_source_snapshot('TEST-JOB',rows)
    result=svc.generate('PLC','8','8',generation_id='TEST-JOB')
    assert result['candidate_count']==3
    item=svc.scenarios.scenario(result['scenario_ids'][0])
    assert item['evidence'][0]['meta']['source_label']
    assert item['evidence'][0]['meta']['lifecycle_reason']
    page=client.get('/quality-scenarios/generate?preview=1&source=operations&product_code=PLC&year=2026&ipmt=FA')
    assert page.status_code==200 and '无漏测分析，基于彻底解决单' in page.text
    assert '/materials/software-operations/MAT-' in page.text
    detail=client.get('/quality-scenarios/'+item['scenario_id'])
    assert detail.status_code==200 and '阶段依据' in detail.text
    assert '/materials/cs/' in detail.text
    response=client.post('/quality-scenarios/generate',data={'source':'operations','product_code':'PLC','ipmt':'不存在','selected_ids':ids[0]})
    assert response.status_code==409
    response=client.post('/quality-scenarios/generate',data={'source':'operations','product_code':'PLC','year':'2026','start_month':'8','end_month':'8','selected_ids':ids},follow_redirects=False)
    assert response.status_code==303
    job_id=response.headers['location'].split('job_id=')[1]
    import time
    for _ in range(100):
        job=client.get('/api/quality-scenario-generations/'+job_id).json()
        if job['status'] in ('COMPLETED','PARTIAL','FAILED'):break
        time.sleep(.02)
    assert job['status']=='COMPLETED' and job['candidate_count']==3
    from quality_knowledge.scenario_assets import ScenarioAssets
    facts=ScenarioAssets(svc.scenarios).facts()
    assert facts[ids[0]]['industry']=='测试行业' and facts[ids[0]]['year']=='2026'


def test_no_keyword_forcing_and_three_phase_rules():
    assert '必须优先选择 POWER_LOSS_RETENTION_RECOVERY' not in PROMPT
    assert all(x in PROMPT for x in ('跨系统','持续时长','时间累积','证据不足','operating_conditions'))


def test_candidate_scope_returns_every_latest_workbench_issue(tmp_path):
    app=create_app(tmp_path/'bulk.db');svc=app.state.scenario_generation_service
    repo=MaterialRepository(tmp_path/'bulk.db');group=repo.group('SW-OPS')
    for i in range(275):
        key=f'ITR202608{i:05d}CS'
        repo.add_material(group,key,{'问题信息_彻底解决单号':key,'问题信息_IPMT':'FA',
            '问题信息_客户行业':'锂电','数据运营_KPI计入月份':'2026-08',
            '问题信息_问题描述':f'问题{i}'},'bulk.xlsx','Sheet1',i+1)
    # A newer version is not a new problem and must not replace the whole scope.
    repo.add_material(group,'ITR20260800000CS',{'问题信息_彻底解决单号':'ITR20260800000CS',
        '问题信息_IPMT':'FA','数据运营_KPI计入月份':'2026-08','问题信息_问题描述':'更新描述'},'bulk-v2.xlsx','Sheet1',1)
    rows=operation_records(svc,{'ipmt':'FA','year':'2026','start_month':'8','end_month':'8'})
    counts=operation_scope_counts(svc,{'ipmt':'FA','year':'2026','start_month':'8','end_month':'8'})
    assert len(rows)==275
    assert counts=={'raw_count':276,'distinct_issue_count':275,'matched_count':275,'version_count':1}
    page=TestClient(app).get('/quality-scenarios/generate?preview=1&ipmt=FA&year=2026&start_month=8&end_month=8')
    assert page.status_code==200 and '当前筛选范围 275 个问题' in page.text
    assert '当前筛选命中 275 个问题' in page.text
