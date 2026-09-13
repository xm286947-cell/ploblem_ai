import json
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from builder.ai_client import AIClientError,AIResponse
from quality_knowledge.scenario_assets import ScenarioAssets
from quality_knowledge.scenario_interpretation import MAX_MERGE_REQUEST_CHARS,ScenarioInterpretation
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
        finding.update({'lifecycle_activity':'长稳运行｜连续运行','usage':'客户连续运行设备',
            'systems_devices':'PLC、伺服','scale':'8轴','environment_conditions':'高温、高负载',
            'quality_concern':'连续稳定','customer_language':'设备不能越跑越容易停',
            'impact':'产线停机','information_gaps':'负载曲线待补充'})
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


def test_completed_customer_portrait_can_be_archived_with_trigger_snapshot(tmp_path):
    app,gen,repo,ids,assets,service=setup(tmp_path)
    jid='SCI-ARCHIVE-DEMO';filters={'portrait_mode':'1','industry':'锂电','customer':'先导公司','year':'2026','problem_domain':'HARDWARE'}
    sources=[{'id':'MAT-1','number':'ITR20260101001CS','evidence_mode':'MARKET_PROBLEM_SUPPLEMENT'}]
    result={'summary':'硬件环境工况画像，待评审','findings':[],'unresolved_ids':['MAT-1']}
    with repo.connect() as c:
        c.execute('''INSERT INTO scenario_interpretation(job_id,scope_hash,input_hash,filters_json,input_json,status,progress,result_json,model)
            VALUES(?,?,?,?,?,'COMPLETED','画像完成',?,?)''',(jid,'scope','input',json.dumps(filters,ensure_ascii=False),json.dumps(sources,ensure_ascii=False),json.dumps(result,ensure_ascii=False),'quality-model'))
    client=TestClient(app)
    response=client.post(f'/quality-scenario-interpretations/{jid}/archive',data={'archive_name':'2026锂电先导质量画像','archive_reason':'年度客户质量复盘'},follow_redirects=False)
    assert response.status_code==303 and response.headers['location'].endswith(jid)
    archived=service.get(jid)
    assert archived['archived']==1 and archived['archive_reason']=='年度客户质量复盘'
    assert archived['archive_snapshot']['input_count']==1
    assert {'label':'行业','value':'锂电','key':'industry'} in archived['archive_snapshot']['trigger_conditions']
    assert {'label':'问题领域','value':'硬件','key':'problem_domain'} in archived['archive_snapshot']['trigger_conditions']
    detail=client.get(f'/quality-scenario-interpretations/{jid}')
    assert detail.status_code==200 and '已归档：2026锂电先导质量画像' in detail.text and '年度客户质量复盘' in detail.text
    archive_page=client.get('/quality-scenario-archives')
    assert archive_page.status_code==200 and '质量画像归档' in archive_page.text
    assert '先导公司' in archive_page.text and '1 个问题' in archive_page.text


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
    assert '从失败步骤继续' in detail and '放弃断点，全部重新生成' in detail
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
    assert records[0]['systems_devices_evidence']=='H3U'
    assert '高温现场运行' in records[0]['environment_evidence']
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
    assert 'name="year"' in page.text and 'name="start_month"' in page.text and 'name="end_month"' in page.text


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


def test_failed_third_level_merge_resumes_without_rerunning_completed_chunks(tmp_path,monkeypatch):
    app,gen,repo,ids,assets,service=setup(tmp_path)
    records=[{'id':f'R{i:02d}','number':f'ITR20260000{i:03d}','description':f'问题{i}',
        'evidence_mode':'MARKET_PROBLEM_SUPPLEMENT','scenarios':[]} for i in range(17)]
    jid='SCI-RESUME-THIRD'
    with repo.connect() as c:
        c.execute('''INSERT INTO scenario_interpretation(job_id,scope_hash,input_hash,filters_json,input_json,status,progress)
            VALUES(?,?,?,?,?,'RUNNING','测试第三层归并')''',(jid,'scope','input',json.dumps({'portrait_mode':'1'}),json.dumps(records)))
    class FailThirdLevel(FakeClient):
        def complete(self,messages):
            payload=json.loads(messages[-1]['content'])
            if payload.get('mode')=='MERGE' and payload.get('merge_level')==2:
                self.calls+=1
                return AIResponse('{"summary":"第三层截断","findings":[','failed-third',{})
            return super().complete(messages)
    failed_client=FailThirdLevel();service.client=failed_client;service.run(jid)
    failed=service.get(jid)
    assert failed['status']=='FAILED'
    assert sum(chunk['status']=='COMPLETED' for chunk in failed['chunks'])==24
    assert any(chunk['chunk_type'].startswith('第3层归并') and chunk['status']=='FAILED' for chunk in failed['chunks'])

    resumed_client=FakeClient();service.client=resumed_client
    monkeypatch.setattr('quality_knowledge.scenario_interpretation.threading.Thread',lambda target,args,**kw:SimpleNamespace(start=lambda:target(*args)))
    service.resume(jid)
    resumed=service.get(jid)
    assert resumed['status']=='COMPLETED' and resumed_client.calls==1
    assert len(resumed['chunks'])==25 and all(chunk['status']=='COMPLETED' for chunk in resumed['chunks'])


def test_merge_uses_short_wire_ids_and_restores_real_evidence_ids(tmp_path):
    app,gen,repo,ids,assets,service=setup(tmp_path)
    real_ids=['MAT-'+('a'*32),'MAT-'+('b'*32)]
    class InspectWireIds(FakeClient):
        def complete(self,messages):
            payload=json.loads(messages[-1]['content'])
            sent=[source for analysis in payload['analyses'] for finding in analysis['findings'] for source in finding['evidence_ids']]
            assert sent==['E1','E2']
            return super().complete(messages)
    finding={key:'归并内容' for key in ('title','observation','why','escape','boundaries','design','test','metrics')};finding['evidence_ids']=real_ids
    result,_=service.complete_with_schema_retry(InspectWireIds(),{'mode':'MERGE','merge_level':2,'analyses':[{'summary':'A','findings':[finding],'unresolved_ids':[]}]},real_ids)
    assert result['findings'][0]['evidence_ids']==real_ids


def test_long_completed_summaries_are_compacted_before_final_merge(tmp_path):
    app,gen,repo,ids,assets,service=setup(tmp_path)
    sources=[]
    for group in range(2):
        findings=[]
        for index in range(5):
            source=f'MAT-{group}-{index}-'+('x'*32)
            finding={key:(f'{key}-'+('很长的历史归并结论'*80)) for key in (
                'title','lifecycle_activity','usage','systems_devices','scale','environment_conditions',
                'quality_concern','customer_language','impact','information_gaps','observation','why',
                'escape','boundaries','design','test','metrics')}
            finding['evidence_ids']=[source];findings.append(finding);sources.append(source)
        analysis={'summary':'历史分批摘要'*1000,'findings':findings,'unresolved_ids':[]}
        compact=service.merge_view(analysis,3)
        assert set(service.chunk_input_ids([compact]))==set(service.chunk_input_ids([analysis]))
        if group==0:analyses=[compact]
        else:analyses.append(compact)

    class InspectBoundedMerge(FakeClient):
        def complete(self,messages):
            assert sum(len(message['content']) for message in messages)<=MAX_MERGE_REQUEST_CHARS
            return super().complete(messages)
    result,_=service.complete_with_schema_retry(InspectBoundedMerge(),
        {'mode':'MERGE','merge_level':3,'analyses':analyses},set(sources))
    assert set(result['findings'][0]['evidence_ids'])==set(sources)


def test_merge_context_budget_has_clear_diagnostic(tmp_path):
    app,gen,repo,ids,assets,service=setup(tmp_path)
    text=service.diagnostic_error(ValueError('归并输入预算超限（25938 token）'))
    assert text.startswith('模型输入/上下文预算超限')


def test_large_real_id_set_is_batched_by_short_merge_wire_ids(tmp_path):
    app,gen,repo,ids,assets,service=setup(tmp_path)
    real_ids=[f'MAT-{index:03d}-'+('x'*80) for index in range(300)]
    finding={key:'归并内容' for key in ('title','observation','why','escape','boundaries','design','test','metrics')}
    finding['evidence_ids']=real_ids
    analysis=service.merge_view({'summary':'旧摘要','findings':[finding],'unresolved_ids':[]},3)
    assert len(json.dumps(analysis,ensure_ascii=False))>12000
    assert service.batches([analysis],limit=12000,max_items=2,strict_single=False)==[[analysis]]
    result,_=service.complete_with_schema_retry(FakeClient(),
        {'mode':'MERGE','merge_level':3,'analyses':[analysis]},set(real_ids))
    assert set(result['findings'][0]['evidence_ids'])==set(real_ids)


def test_fourth_level_overlong_sections_retry_then_converge_without_losing_evidence(tmp_path):
    app,gen,repo,ids,assets,service=setup(tmp_path)
    class StillVerbose:
        def __init__(self):self.calls=0;self.prompts=[]
        def complete(self,messages):
            self.calls+=1;self.prompts.append(messages[0]['content'])
            finding={key:'结论'*100 for key in (
                'title','lifecycle_activity','usage','systems_devices','scale','environment_conditions',
                'quality_concern','customer_language','impact','information_gaps','observation','why',
                'escape','boundaries','design','test','metrics')}
            finding['evidence_ids']=['E1']
            return AIResponse(json.dumps({'summary':'第四层归并','findings':[finding],'unresolved_ids':[]},ensure_ascii=False),'verbose-model',{})
    lower={key:'下层结论' for key in ('title','observation','why','escape','boundaries','design','test','metrics')}
    lower['evidence_ids']=['MAT-REAL-ID']
    client=StillVerbose()
    result,_=service.complete_with_schema_retry(client,
        {'mode':'MERGE','merge_level':3,'analyses':[{'summary':'A','findings':[lower],'unresolved_ids':[]}]},{'MAT-REAL-ID'})
    assert client.calls==2 and '严格不超过90个汉字' in client.prompts[1]
    assert all(len(result['findings'][0][key])<=140 for key in (
        'title','lifecycle_activity','usage','systems_devices','scale','environment_conditions',
        'quality_concern','customer_language','impact','information_gaps','observation','why',
        'escape','boundaries','design','test','metrics'))
    assert result['findings'][0]['evidence_ids']==['MAT-REAL-ID']
    assert '超长段落已' in result['summary']


def test_customer_portrait_can_switch_product_groups_without_mixing_models(tmp_path):
    app,gen,repo,ids,assets,service=setup(tmp_path)
    samples=(('ITR20261213001CS','PLC','AM600','软件'),('ITR20261213002CS','iFA','iFA Evolution','软件'),
             ('ITR20261213003CS','伺服','SV680','硬件'))
    for index,(key,product_type,model,domain) in enumerate(samples,1):
        repo.add_material(repo.group('ITR-CS'),key,{'问题信息_问题描述':f'{product_type}画像问题',
            '问题信息_客户行业':'锂电','问题信息_客户名称':'先导公司','问题信息_产品类型':product_type,
            '问题信息_产品型号':model,'问题信息_问题领域':domain},'product-groups.xlsx','Sheet1',index)
    client=TestClient(app)
    overview=client.get('/quality-scenario-assets/portrait?industry=锂电&customer=先导公司')
    assert overview.status_code==200
    assert all(text in overview.text for text in ('按产品分类查看','PLC','iFA','伺服','产品分类画像'))
    plc=client.get('/quality-scenario-assets/portrait?industry=锂电&customer=先导公司&product_group=PLC')
    assert plc.status_code==200 and 'AM600' in plc.text
    scope=service.portrait_scope({'industry':'锂电','customer':'先导公司','product_group':'PLC','portrait_mode':'1'})
    assert len(scope)==1 and scope[0]['product_group']=='PLC' and scope[0]['product']=='AM600'
