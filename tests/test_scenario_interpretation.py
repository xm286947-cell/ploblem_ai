import json
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from builder.ai_client import AIClientError,AIResponse
from quality_knowledge.scenario_assets import ScenarioAssets
from quality_knowledge.scenario_interpretation import ScenarioInterpretation
from test_scenario_operation_sources import seed


def setup(tmp_path):
    app,gen,repo,ids=seed(tmp_path)
    scenes=gen.scenarios
    sid=scenes.save_scenario('',{'scenario_code':'TEST','name':'持续运行响应及时性','product_code':'PLC','preconditions':'连续运行72小时','trigger_conditions':'资源持续累积'}, {})
    with repo.connect() as c:
        for kid in ('K0','K1',ids[2]):
            c.execute('INSERT INTO quality_scenario_evidence VALUES(?,?,?)',(sid,kid,'{}'))
    assets=ScenarioAssets(scenes)
    return app,gen,repo,ids,assets,ScenarioInterpretation(assets,gen)


class FakeClient:
    def __init__(self):self.calls=0
    def complete(self,messages):
        self.calls+=1
        payload=json.loads(messages[1]['content'])
        ids=[x['id'] for x in payload['records']] if payload['mode']=='ANALYSE' else sorted({i for x in payload['analyses'] for f in x['findings'] for i in f['evidence_ids']})
        finding={k:'合成测试结论' for k in ('title','observation','why','escape','boundaries','design','test','metrics')}
        finding['evidence_ids']=ids
        return AIResponse(json.dumps({'summary':'合成测试，待评审','findings':[finding],'unresolved_ids':[]}), 'fake-model', {})


def test_historical_kpi_environment_and_no_publication_needed(tmp_path):
    app,gen,repo,ids,assets,service=setup(tmp_path)
    facts=assets.facts()
    assert facts['K0']['year']=='2026' and facts['K0']['month']=='8'
    assert facts['K0']['period_status']=='年月完整'
    assert facts['K0']['cs_context']['技术根因分析与纠正_TRC纠正信息']=='修复资源释放'
    assert assets.report()['unknown_time_count']==0
    assert all(r['environment']=='连续运行72小时；资源持续累积' for r in assets.report()['records'])
    key='ITR20250100000CS'
    repo.add_material(repo.group('SW-OPS'),key,{'数据运营_KPI计入月份':'9月'},'synthetic.xlsx','test',1)
    assert assets.facts()['K0']['period_status']=='月份已知、年份缺失'
    assert assets.facts()['K0']['year']=='未知'
    assert assets.report()['unknown_time_count']==1
    assert assets.report()['assets'][0]['status']!='PUBLISHED'


def test_saved_manual_generation_coverage_stale_and_no_get_calls(tmp_path,monkeypatch):
    app,gen,repo,ids,assets,service=setup(tmp_path)
    fake=FakeClient();service.client=fake
    # Deterministically run the real worker in the test thread.
    monkeypatch.setattr('quality_knowledge.scenario_interpretation.threading.Thread',lambda target,args,**kw:SimpleNamespace(start=lambda:target(*args)))
    jid=service.start({})
    job=service.get(jid)
    assert job['status']=='COMPLETED' and fake.calls==1
    assert {i for f in job['result']['findings'] for i in f['evidence_ids']}=={'K0','K1',ids[2]}
    assert service.start({})==jid and fake.calls==1
    client=TestClient(app)
    response=client.post('/quality-scenario-interpretations',data={},follow_redirects=False)
    assert response.status_code==303 and response.headers['location'].endswith(jid)
    for _ in range(2):
        page=client.get('/quality-scenario-interpretations/'+jid)
        assert page.status_code==200 and '整体判断' in page.text
        assert '连续运行72小时' in page.text and r'\u8fde\u7eed\u8fd0\u884c' not in page.text
        assert client.get('/api/quality-scenario-interpretations/'+jid).json()['status']=='COMPLETED'
    assert fake.calls==1
    assets.save_context(assets.catalog()[0]['scenario_id'],{'environment':'高负载'})
    assert '尚未反映新数据' in client.get('/quality-scenario-interpretations/'+jid).text
    assert fake.calls==1
    assert client.get('/quality-scenario-assets').status_code==200


def test_batches_merge_and_reject_missing_or_invented_evidence(tmp_path,monkeypatch):
    app,gen,repo,ids,assets,service=setup(tmp_path);fake=FakeClient();service.client=fake
    monkeypatch.setattr('quality_knowledge.scenario_interpretation.threading.Thread',lambda target,args,**kw:SimpleNamespace(start=lambda:target(*args)))
    original=service.batches
    service.batches=lambda items,**kwargs: [[x] for x in items] if items and 'id' in items[0] else original(items,**kwargs)
    jid=service.start({})
    assert service.get(jid)['status']=='COMPLETED' and fake.calls==4
    covered,_=service.complete(fake,{'mode':'ANALYSE','records':[{'id':'K0'}]},['K0','K1'])
    assert covered['unresolved_ids']==['K1'] and '未静默遗漏' in covered['summary']
    with pytest.raises(ValueError,match='来源引用'):
        service.complete(fake,{'mode':'ANALYSE','records':[{'id':'FORGED'}]},['K0'])
    with pytest.raises(ValueError,match='未截断'):original([{'text':'x'*23000}])
    class Bad:
        def complete(self,messages):return AIResponse('{"summary":', 'bad', {})
    service.client=Bad()
    failed=service.start({},refresh=True)
    failed_job=service.get(failed)
    assert failed_job['status']=='FAILED' and '模型返回JSON不完整' in failed_job['error']
    assert not failed_job['result']


def test_readable_itr_reference_is_normalized_to_internal_source_id(tmp_path):
    app,gen,repo,ids,assets,service=setup(tmp_path)
    class NumberClient:
        def complete(self,messages):
            record=json.loads(messages[-1]['content'])['records'][0]
            finding={k:'测试' for k in ('title','observation','why','escape','boundaries','design','test','metrics')}
            finding['evidence_ids']=[record['number']]
            return AIResponse(json.dumps({'summary':'按ITR引用','findings':[finding],'unresolved_ids':[]},ensure_ascii=False),'number-model',{})
    payload={'mode':'ANALYSE','records':[{'id':'MAT-INTERNAL','number':'ITR20260101001CS'}]}
    result,_=service.complete(NumberClient(),payload,['MAT-INTERNAL'])
    assert result['findings'][0]['evidence_ids']==['MAT-INTERNAL']


def test_failed_portrait_shows_real_reason_and_failed_chunk(tmp_path,monkeypatch):
    app,gen,repo,ids,assets,service=setup(tmp_path)
    class Broken:
        def complete(self,messages):raise RuntimeError('proxy connection refused at 127.0.0.1:7897')
    service.client=Broken()
    monkeypatch.setattr('quality_knowledge.scenario_interpretation.threading.Thread',lambda target,args,**kw:SimpleNamespace(start=lambda:target(*args)))
    filters={'industry':'测试行业','customer':'测试客户','portrait_mode':'1'}
    jid=service.start(filters);job=service.get(jid)
    assert job['status']=='FAILED' and 'proxy connection refused' in job['error']
    assert job['chunks'][0]['status']=='FAILED'
    client=TestClient(app)
    detail=client.get('/quality-scenario-interpretations/'+jid).text
    assert '分批处理状态' in detail and 'proxy connection refused' in detail
    portrait=client.get('/quality-scenario-assets/portrait?industry=测试行业&customer=测试客户').text
    assert '失败原因' in portrait and 'proxy connection refused' in portrait


def test_stop_prevents_late_result_and_allows_retry(tmp_path,monkeypatch):
    app,gen,repo,ids,assets,service=setup(tmp_path)
    monkeypatch.setattr('quality_knowledge.scenario_interpretation.threading.Thread',lambda **kw:SimpleNamespace(start=lambda:None))
    jid=service.start({})
    assert service.start({})==jid
    response=TestClient(app).post('/quality-scenario-interpretations/'+jid+'/stop',follow_redirects=False)
    assert response.status_code==303
    service.update(jid,status='COMPLETED',result_json='{}')
    assert service.get(jid)['status']=='FAILED'
    assert service.start({})!=jid


def test_portrait_market_supplement_requires_scope_and_runs_one_issue_per_chunk(tmp_path,monkeypatch):
    app,gen,repo,ids,assets,service=setup(tmp_path)
    key='ITR20250909099CS'
    repo.add_material(repo.group('ITR-CS'),key,{'问题信息_问题描述':'高温现场运行后器件异常','问题信息_客户行业':'锂电',
        '问题信息_客户名称':'客户甲','问题信息_产品型号':'H3U','问题信息_问题领域':'硬件',
        '技术根因分析与纠正_器件类别':'电容','技术根因分析与纠正_器件失效模式':'容量衰减'},'supplement.xlsx','Sheet1',1)
    with pytest.raises(ValueError,match='必须先指定行业或客户'):
        service.snapshot({'supplement_market':'1','portrait_mode':'1'})
    filters,records,_=service.snapshot({'industry':'锂电','supplement_market':'1','problem_domain':'HARDWARE','portrait_mode':'1'})
    assert filters['portrait_mode']=='1' and len(records)==1
    assert records[0]['evidence_mode']=='MARKET_PROBLEM_SUPPLEMENT' and records[0]['product']=='H3U'
    fake=FakeClient();service.client=fake
    monkeypatch.setattr('quality_knowledge.scenario_interpretation.threading.Thread',lambda target,args,**kw:SimpleNamespace(start=lambda:target(*args)))
    jid=service.start(filters)
    job=service.get(jid)
    assert job['status']=='COMPLETED' and fake.calls==1
    assert len(job['chunks'])==1 and job['chunks'][0]['chunk_type']=='单问题补充提取'


def test_portrait_queries_cs_itr_database_before_existing_scenes(tmp_path):
    app,gen,repo,ids,assets,service=setup(tmp_path)
    key='ITR20261212001CS'
    repo.add_material(repo.group('ITR-CS'),key,{'问题信息_问题描述':'现场高温下模块重启',
        '问题信息_客户行业':'锂电','问题信息_客户名称':'客户乙','问题信息_产品型号':'H5U',
        '问题信息_问题领域':'硬件'},'portrait.xlsx','Sheet1',1)
    filters,records,_=service.snapshot({'industry':'锂电','customer':'客户乙','portrait_mode':'1'})
    assert len(records)==1 and records[0]['number']==key
    assert records[0]['evidence_mode']=='MARKET_PROBLEM_SUPPLEMENT'
    page=TestClient(app).get('/quality-scenario-assets/portrait?industry=锂电&customer=客户乙')
    assert page.status_code==200
    assert '数据库命中问题' in page.text and '现场高温下模块重启' in page.text
    assert '尚无场景，AI待提炼' in page.text


def test_portrait_choices_prioritize_scene_evidence_and_cascade_company(tmp_path):
    app,gen,repo,ids,assets,service=setup(tmp_path)
    for i in range(5):
        key=f'ITR2026122{i:04d}CS'
        repo.add_material(repo.group('ITR-CS'),key,{'问题信息_问题描述':'未沉淀场景问题',
            '问题信息_客户行业':'行业乙','问题信息_客户名称':'客户乙','问题信息_产品型号':'M2'},'portrait.xlsx','Sheet1',i+1)
    client=TestClient(app)
    page=client.get('/quality-scenario-assets/portrait')
    assert page.status_code==200
    # 测试行业关联了场景，尽管问题数少于行业乙，仍排在前面。
    assert page.text.index('测试行业（场景关联') < page.text.index('行业乙（场景关联')
    cascaded=client.get('/quality-scenario-assets/portrait?industry=行业乙').text
    company_select=cascaded.split('name="customer"',1)[1].split('</select>',1)[0]
    assert '客户乙' in company_select and '测试客户' not in company_select


def test_portrait_has_full_scope_digest_and_actionable_summary(tmp_path):
    app,gen,repo,ids,assets,service=setup(tmp_path)
    for i,domain in enumerate(('软件','硬件','硬件')):
        key=f'ITR2026110{i:04d}CS'
        repo.add_material(repo.group('ITR-CS'),key,{'问题信息_问题描述':f'画像问题{i}',
            '问题信息_客户行业':'画像行业','问题信息_客户名称':'画像客户',
            '问题信息_产品型号':'P1' if i<2 else 'P2','问题信息_问题领域':domain,
            '问题信息_问题原因定位':'设计原因' if i==0 else ''},'portrait-summary.xlsx','Sheet1',i+1)
    text=TestClient(app).get('/quality-scenario-assets/portrait?industry=画像行业&customer=画像客户').text
    assert '当前范围结论与下一步' in text
    assert '产品 × 问题领域' in text
    assert 'Top产品' in text and 'P1' in text and 'P2' in text
    assert '根因信息 1/3' in text


def test_merge_invalid_json_retries_with_compact_instruction(tmp_path):
    app,gen,repo,ids,assets,service=setup(tmp_path)
    class TruncatedThenCompact:
        def __init__(self):self.calls=0;self.system_prompts=[]
        def complete(self,messages):
            self.calls+=1;self.system_prompts.append(messages[0]['content'])
            if self.calls==1:return AIResponse('{"summary":"第二层归并未闭合","findings":[', 'merge-model', {})
            payload=json.loads(messages[-1]['content'])
            allowed=sorted({source for analysis in payload['analyses'] for finding in analysis['findings'] for source in finding['evidence_ids']})
            finding={key:'紧凑归并结论' for key in ('title','observation','why','escape','boundaries','design','test','metrics')}
            finding['evidence_ids']=allowed
            return AIResponse(json.dumps({'summary':'紧凑重试成功','findings':[finding],'unresolved_ids':[]},ensure_ascii=False),'merge-model',{})
    client=TruncatedThenCompact()
    finding={key:'下层结论' for key in ('title','observation','why','escape','boundaries','design','test','metrics')}
    finding['evidence_ids']=['K0','K1']
    result,model=service.complete_with_schema_retry(client,{'mode':'MERGE','analyses':[{'summary':'A','findings':[finding],'unresolved_ids':[]}]},{'K0','K1'})
    assert result['summary']=='紧凑重试成功' and model=='merge-model' and client.calls==2
    assert '上一次返回未形成完整合法JSON' in client.system_prompts[1]


def test_merge_batches_limit_item_count(tmp_path):
    app,gen,repo,ids,assets,service=setup(tmp_path)
    items=[{'summary':str(i),'findings':[],'unresolved_ids':[]} for i in range(9)]
    groups=service.batches(items,limit=12000,max_items=4)
    assert [len(group) for group in groups]==[4,4,1]


def test_explicit_output_token_limit_uses_compact_retry(tmp_path):
    app,gen,repo,ids,assets,service=setup(tmp_path)
    class TokenLimitedThenValid(FakeClient):
        def complete(self,messages):
            if not any('上一次返回未形成完整合法JSON' in message['content'] for message in messages):
                raise AIClientError('AI响应达到输出Token上限(max_tokens=8192)，JSON可能被截断')
            return super().complete(messages)
    finding={key:'下层结论' for key in ('title','observation','why','escape','boundaries','design','test','metrics')};finding['evidence_ids']=['K0']
    result,_=service.complete_with_schema_retry(TokenLimitedThenValid(),{'mode':'MERGE','analyses':[{'summary':'A','findings':[finding],'unresolved_ids':[]}]},{'K0'})
    assert result['findings'][0]['evidence_ids']==['K0']
