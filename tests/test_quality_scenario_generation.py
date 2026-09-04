import json

from fastapi.testclient import TestClient

from builder.ai_client import AIResponse
from quality_knowledge.scenario_generation import ScenarioGenerationService
from quality_knowledge.scenarios import ScenarioRepository
from quality_knowledge.materials import MaterialRepository
from quality_knowledge.web.app import create_app


class FakeIssues:
    def query_issues(self, filters, limit):
        return [
            {'knowledge_id':'QK-1','business_issue_id':'ITR001','business_type':'PLC','title':'在线监控卡顿','description':'变量较多时卡顿','month':'6月','severity':'H'},
            {'knowledge_id':'QK-2','business_issue_id':'ITR002','business_type':'PLC','title':'长期监控越来越慢','description':'持续运行后刷新变慢','month':'6月','severity':'M'},
        ]

    def get_latest_analysis(self, knowledge_id, stage):
        values={
            'occurrence':{'root_cause_summary':{'value':'刷新任务阻塞'}},
            'escape':{'escape_cause_summary':{'value':'未覆盖大变量长时间监控'}},
            'recurrence':{'recurrence_risk_level':'HIGH'},
            'capability_gap':{'capability_gaps':[{'dimension':'TECHNICAL','category':'TEST_CAPABILITY','description':'缺少性能场景'}]},
        }
        return {'result':values[stage]}


class FakeClient:
    def __init__(self): self.calls=0
    def complete(self, messages):
        self.calls+=1
        payload={"items":[{
            "name":"大型PLC工程在线监控流畅性","lifecycle_code":"SOFTWARE_DEBUGGING","activity_code":"ONLINE_MONITORING",
            "scenario_chain":"下载运行→在线监控→大量变量刷新→界面卡顿→调试效率下降",
            "experience_requirement":"在线调试持续流畅","concern_points":"卡顿、响应慢","quality_attribute":"性能效率","quality_subcharacteristic":"时间特性、资源利用率",
            "applicable_boundary":"大型工程、大量变量、持续监控","validation_direction":"验证P95响应时间和长稳退化",
            "measurement_suggestion":"记录操作响应时间，计算P95；固定变量数量连续观测2小时",
            "evidence_issue_ids":["QK-1","QK-2"],"evidence_summary":"两条问题均指向大变量监控性能退化","confidence":0.88,
            "confirmation_questions":["变量数量边界是多少"]
        }]}
        return AIResponse(json.dumps(payload,ensure_ascii=False),'scenario-model',{})


def test_generate_candidates_are_review_only_and_traceable(tmp_path):
    repository=ScenarioRepository(tmp_path/'scenario.db');client=FakeClient()
    service=ScenarioGenerationService(FakeIssues(),repository,tmp_path,client)
    result=service.generate('PLC','1月','12月')
    assert result['candidate_count']==1 and result['source_issue_count']==2
    item=repository.scenario(result['scenario_ids'][0])
    assert item['status']=='IN_REVIEW'
    assert {x['knowledge_id'] for x in item['evidence']}=={'QK-1','QK-2'}
    assert repository.generations()[0]['model_name']=='scenario-model'
    assert repository.generations()[0]['linked_candidate_count']==1
    assert [x['scenario_id'] for x in repository.scenarios(generation_id=result['generation_id'])]==result['scenario_ids']
    assert item['scenario_chain']=='下载运行 → 在线监控 → 变量观察 → 状态分析 → 调整'
    assert item['quality_subcharacteristic']=='时间特性、资源利用率'
    assert '计算P95' in item['measurement_suggestion']


def test_generation_precheck_requires_existing_analysis(tmp_path):
    service=ScenarioGenerationService(FakeIssues(),ScenarioRepository(tmp_path/'scenario.db'),tmp_path,FakeClient())
    check=service.precheck('PLC','1月','12月')
    assert {key:check[key] for key in ('issue_count','analysed_count','ready','coverage_rate')}=={'issue_count':2,'analysed_count':2,'ready':True,'coverage_rate':100.0}
    assert len(check['items'])==2


def test_generation_precheck_uses_description_when_title_is_empty(tmp_path):
    class EmptyTitleIssues(FakeIssues):
        def query_issues(self,filters,limit):
            rows=super().query_issues(filters,limit);rows[0]['title']='';return rows
    check=ScenarioGenerationService(EmptyTitleIssues(),ScenarioRepository(tmp_path/'scenario.db'),tmp_path,FakeClient()).precheck('PLC','1月','12月')
    assert check['items'][0]['title']=='变量较多时卡顿'


def test_generation_page_is_not_swallowed_by_scenario_detail_route(tmp_path):
    client=TestClient(create_app(tmp_path/'web.db'))
    page=client.get('/quality-scenarios/generate')
    assert page.status_code==200
    assert 'AI生成质量场景候选' in page.text and '最近生成记录' in page.text
    assert '/quality-scenarios/generate' in client.get('/quality-scenarios').text


def test_selected_issue_scope_and_itr_cs_context_are_used(tmp_path):
    db=tmp_path/'scenario.db';repository=ScenarioRepository(db);MaterialRepository(db)
    raw={'问题信息_IPMT':'控制IPMT','问题信息_SPDT':'PLC SPDT','问题信息_产品型号':'AM600','问题信息_客户行业':'锂电','问题信息_客户名称':'示例客户','问题信息_客户分级':'战略客户','问题信息_当前客户状态':'复位可恢复','问题信息_问题发生阶段':'现场调试','技术根因分析与纠正_TRC纠正信息':'修复刷新调度'}
    with repository.connect() as c:
        c.execute("INSERT INTO source_material(material_id,group_id,material_type,business_key,canonical_itr,version_no,source_hash,raw_json) VALUES('MAT-CS','DG-ITR-CS','ITR_CS','ITR001CS','ITR001',1,'H',?)",(json.dumps(raw,ensure_ascii=False),))
        c.execute("INSERT INTO issue_material_link(link_id,knowledge_id,material_id,link_status) VALUES('L1','QK-1','MAT-CS','LINKED')")
    service=ScenarioGenerationService(FakeIssues(),repository,tmp_path,FakeClient())
    records=service._records('PLC','1月','12月',['QK-1'])
    assert len(records)==1 and records[0]['itr_cs_context']['trc_correction']=='修复刷新调度'
    result=service.generate('PLC','1月','12月',selected_ids=['QK-1'])
    item=repository.scenario(result['scenario_ids'][0])
    assert item['scopes']=={'IPMT':['控制IPMT'],'SPDT':['PLC SPDT'],'PRODUCT_MODEL':['AM600'],'INDUSTRY':['锂电'],'CUSTOMER_NAME':['示例客户'],'CUSTOMER_LEVEL':['战略客户'],'CUSTOMER_STATUS':['复位可恢复'],'OCCURRENCE_PHASE':['现场调试']}
    assert [x['knowledge_id'] for x in item['evidence']]==['QK-1']
    with repository.connect() as c:c.execute("DELETE FROM quality_scenario_scope WHERE scenario_id=?",(item['scenario_id'],))
    reloaded=ScenarioRepository(db).scenario(item['scenario_id'])
    assert reloaded['scopes']['INDUSTRY']==['锂电'] and reloaded['scopes']['CUSTOMER_STATUS']==['复位可恢复']


def test_generated_candidate_warns_when_published_scenario_is_similar(tmp_path):
    repository=ScenarioRepository(tmp_path/'scenario.db')
    repository.save_scenario('',{'scenario_code':'OLD-1','name':'大型PLC工程在线监控流畅性','lifecycle_code':'SOFTWARE_DEBUGGING','activity_code':'ONLINE_MONITORING','experience_requirement':'持续流畅','concern_points':'卡顿','quality_attribute':'性能效率','applicable_boundary':'大型工程','validation_direction':'性能验证','status':'PUBLISHED'},{})
    result=ScenarioGenerationService(FakeIssues(),repository,tmp_path,FakeClient()).generate('PLC','1月','12月')
    candidate=repository.scenario(result['scenario_ids'][0])
    assert candidate['duplicates'] and candidate['duplicates'][0]['status']=='PUBLISHED'


def test_ai_chinese_taxonomy_and_business_issue_id_are_normalized(tmp_path):
    class AliasClient:
        def complete(self,messages):
            item={"items":[{"name":"在线监控流畅性","lifecycle_code":"软件调试","activity_code":"程序在线监控与调试","experience_requirement":"流畅","concern_points":"卡顿","quality_attribute":"性能效率","applicable_boundary":"大型工程","validation_direction":"长稳性能","evidence_issue_ids":["ITR001"],"evidence_summary":"来源明确","confidence":0.8,"confirmation_questions":[]}]}
            return AIResponse(json.dumps(item,ensure_ascii=False),'alias-model',{})
    repository=ScenarioRepository(tmp_path/'scenario.db')
    result=ScenarioGenerationService(FakeIssues(),repository,tmp_path,AliasClient()).generate('PLC','1月','12月',selected_ids=['QK-1'])
    item=repository.scenario(result['scenario_ids'][0])
    assert item['lifecycle_code']=='SOFTWARE_DEBUGGING' and item['activity_code']=='ONLINE_MONITORING'
    assert item['evidence'][0]['knowledge_id']=='QK-1'
    assert item['scenario_chain']=='下载运行 → 在线监控 → 变量观察 → 状态分析 → 调整'


def test_existing_database_adds_new_scenario_fields_and_backfills_chain(tmp_path):
    db=tmp_path/'legacy.db'
    with __import__('sqlite3').connect(db) as c:
        c.execute("CREATE TABLE quality_scenario(scenario_id TEXT PRIMARY KEY,scenario_code TEXT UNIQUE,name TEXT,lifecycle_code TEXT,activity_code TEXT,experience_requirement TEXT,concern_points TEXT,quality_attribute TEXT,applicable_boundary TEXT,validation_direction TEXT,status TEXT,version_no INTEGER,created_at TEXT,updated_at TEXT)")
        c.execute("INSERT INTO quality_scenario VALUES('OLD','OLD-1','旧场景','SOFTWARE_DEBUGGING','ONLINE_MONITORING','','','','','','DRAFT',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
    repository=ScenarioRepository(db);item=repository.scenario('OLD')
    assert item['scenario_chain']=='下载运行 → 在线监控 → 变量观察 → 状态分析 → 调整'
    assert item['quality_subcharacteristic'] is None and item['measurement_suggestion'] is None


def test_business_rules_correct_power_loss_and_terminal_use_misclassification(tmp_path):
    class WrongClassificationClient:
        def complete(self,messages):
            payload={'items':[
                {'name':'掉电保持异常','lifecycle_code':'ENGINEERING_CONFIGURATION','activity_code':'STATE_DATA_PROCESSING','evidence_issue_ids':['QK-P']},
                {'name':'正常使用状态异常','lifecycle_code':'ENGINEERING_CONFIGURATION','activity_code':'CONTROL_PROGRAMMING','evidence_issue_ids':['QK-N']},
            ]}
            return AIResponse(json.dumps(payload,ensure_ascii=False),'rule-model',{})
    repository=ScenarioRepository(tmp_path/'rules.db');service=ScenarioGenerationService(FakeIssues(),repository,tmp_path,WrongClassificationClient())
    records=[
        {'knowledge_id':'QK-P','description':'设备掉电后保持变量丢失，重新上电计数清零','itr_cs_context':{'occurrence_phase':'终端正常使用'}},
        {'knowledge_id':'QK-N','description':'设备正常运行时状态显示异常','itr_cs_context':{'occurrence_phase':'终端正常使用'}},
    ]
    items,_=service._complete(WrongClassificationClient(),records,{'QK-P':'QK-P','QK-N':'QK-N'},repository.taxonomy_active())
    assert items[0]['lifecycle_code']=='RUNTIME_EXECUTION' and items[0]['activity_code']=='POWER_LOSS_RETENTION_RECOVERY'
    assert items[1]['lifecycle_code']=='RUNTIME_EXECUTION' and items[1]['activity_code']=='STATE_DATA_PROCESSING'
    assert '请确认实际业务活动' in items[1]['confirmation_questions'][0]


def test_generation_status_endpoint_exposes_progress_and_failure(tmp_path):
    client=TestClient(create_app(tmp_path/'web.db'));repository=client.app.state.scenario_repository
    repository.create_generation('QSG-STATUS','PLC','1月','12月',10,'TEST')
    repository.update_generation('QSG-STATUS',status='FAILED',progress_text='生成失败',error_message='模型响应格式错误')
    status=client.get('/api/quality-scenario-generations/QSG-STATUS').json()
    assert status['status']=='FAILED' and status['error_message']=='模型响应格式错误'
    page=client.get('/quality-scenarios/generate?job_id=QSG-STATUS')
    assert 'scenario-job-status' in page.text and '状态查询失败' in page.text


def test_generation_batch_has_direct_candidate_entry(tmp_path):
    db=tmp_path/'web.db';client=TestClient(create_app(db));repository=client.app.state.scenario_repository
    repository.create_generation('QSG-LINK','PLC','1月','12月',2,'TEST')
    repository.save_generated_candidate('AI-LINK',{'name':'在线监控流畅性','lifecycle_code':'SOFTWARE_DEBUGGING','activity_code':'ONLINE_MONITORING','experience_requirement':'流畅','concern_points':'卡顿','quality_attribute':'性能效率','applicable_boundary':'大型工程','validation_direction':'性能验证','evidence_summary':'证据','confidence':0.8,'confirmation_questions':[],'evidence_issue_ids':['QK-1']},{},'QSG-LINK','PLC','1月','12月','model-x')
    repository.update_generation('QSG-LINK',status='COMPLETED',candidate_count=1,model_name='model-x')
    history=client.get('/quality-scenarios/generate').text
    assert '/quality-scenarios?generation_id=QSG-LINK' in history and '已关联 1' in history and '查看本批候选' in history
    listing=client.get('/quality-scenarios?generation_id=QSG-LINK')
    assert listing.status_code==200 and '在线监控流畅性' in listing.text and '本次生成候选' in listing.text


def test_generation_without_candidate_still_has_task_detail_button(tmp_path):
    client=TestClient(create_app(tmp_path/'web.db'));repository=client.app.state.scenario_repository
    repository.create_generation('QSG-EMPTY','PLC','1月','12月',2,'TEST')
    repository.update_generation('QSG-EMPTY',status='FAILED',error_message='没有有效候选')
    page=client.get('/quality-scenarios/generate')
    assert '/quality-scenarios/generate?job_id=QSG-EMPTY' in page.text and '查看任务详情' in page.text


def test_ai_input_uses_compact_analysis_instead_of_full_stage_payload(tmp_path):
    class HugeIssues(FakeIssues):
        def get_latest_analysis(self,knowledge_id,stage):
            result=super().get_latest_analysis(knowledge_id,stage)
            result['result']['unused_large_field']='X'*50000
            return result
    records=ScenarioGenerationService(HugeIssues(),ScenarioRepository(tmp_path/'scenario.db'),tmp_path,FakeClient())._records('PLC','1月','12月',['QK-1'])
    encoded=json.dumps(records,ensure_ascii=False)
    assert 'unused_large_field' not in encoded and len(encoded)<5000


def test_legacy_completed_zero_candidate_is_migrated_to_failed(tmp_path):
    db=tmp_path/'scenario.db';repository=ScenarioRepository(db)
    repository.create_generation('QSG-OLD','IFA','1月','7月',1,'TEST')
    repository.update_generation('QSG-OLD',status='COMPLETED',candidate_count=0,model_name='old-model')
    migrated=ScenarioRepository(db).generation('QSG-OLD')
    assert migrated['status']=='FAILED' and '重新生成' in migrated['error_message']


def test_missing_issue_is_retried_and_coverage_is_complete(tmp_path):
    class RetryClient(FakeClient):
        def complete(self,messages):
            self.calls+=1
            records=json.loads(messages[-1]['content'])['records']
            ids=[x['knowledge_id'] for x in records]
            evidence=ids[:1] if len(ids)>1 else ids
            payload={'items':[{'name':'监控场景-'+evidence[0],'lifecycle_code':'SOFTWARE_DEBUGGING','activity_code':'ONLINE_MONITORING','quality_subcharacteristic':'时间特性','evidence_issue_ids':evidence,'confidence':0.8}]}
            return AIResponse(json.dumps(payload,ensure_ascii=False),'retry-model',{})
    repository=ScenarioRepository(tmp_path/'coverage.db');client=RetryClient()
    result=ScenarioGenerationService(FakeIssues(),repository,tmp_path,client).generate('PLC','1月','12月')
    assert client.calls==2
    assert result['status']=='COMPLETED' and result['classified_count']==2 and result['unprocessed_count']==0
    ledger=repository.issue_classifications(result['generation_id'])
    assert {x['knowledge_id'] for x in ledger}=={'QK-1','QK-2'} and {x['status'] for x in ledger}=={'CLASSIFIED'}


def test_unclassified_issue_makes_generation_partial(tmp_path):
    class PartialClient(FakeClient):
        def complete(self,messages):
            records=json.loads(messages[-1]['content'])['records']
            if len(records)==1 and records[0]['knowledge_id']=='QK-2':return AIResponse('{"items":[]}','partial-model',{})
            payload={'items':[{'name':'监控场景','lifecycle_code':'SOFTWARE_DEBUGGING','activity_code':'ONLINE_MONITORING','evidence_issue_ids':['QK-1'],'confidence':0.8}]}
            return AIResponse(json.dumps(payload,ensure_ascii=False),'partial-model',{})
    repository=ScenarioRepository(tmp_path/'partial.db')
    result=ScenarioGenerationService(FakeIssues(),repository,tmp_path,PartialClient()).generate('PLC','1月','12月')
    assert result['status']=='PARTIAL' and result['review_required_count']==1 and result['classified_count']==1
    assert repository.generation(result['generation_id'])['status']=='PARTIAL'


def test_scenario_insights_show_activity_and_industry_views(tmp_path):
    client=TestClient(create_app(tmp_path/'insights.db'));repository=client.app.state.scenario_repository
    scenario_id=repository.save_scenario('',{'scenario_code':'INS-1','name':'掉电恢复','lifecycle_code':'RUNTIME_EXECUTION','activity_code':'POWER_LOSS_RETENTION_RECOVERY','status':'PUBLISHED'},{'INDUSTRY':['锂电']})
    with repository.connect() as c:c.execute("INSERT INTO quality_scenario_evidence VALUES(?,?,?)",(scenario_id,'QK-1','{}'))
    page=client.get('/quality-scenarios/insights')
    assert page.status_code==200 and '业务活动 → 行业差异' in page.text and '行业 → 问题场景' in page.text
    assert '掉电数据保持与上电恢复' in page.text and '锂电' in page.text


def test_structured_fields_industry_variants_and_issue_ledger_page(tmp_path):
    db=tmp_path/'structured.db';repository=ScenarioRepository(db)
    item={'name':'掉电保持场景','lifecycle_code':'RUNTIME_EXECUTION','activity_code':'POWER_LOSS_RETENTION_RECOVERY','failure_mode':'保持变量丢失','failure_mechanism':'存储提交未完成','trigger_conditions':'运行中异常掉电','preconditions':'存在保持变量','affected_object':'PLC运行数据','business_impact':'计数状态丢失','recovery_method':'重新写入参数并重启','evidence_issue_ids':['QK-1'],'confidence':0.9}
    scenario_id=repository.save_generated_candidate('AI-STRUCT',item,{'INDUSTRY':['锂电'],'PRODUCT_MODEL':['AM600']},'QSG-STRUCT','PLC','1月','12月','model-x')
    repository.save_industry_variants(scenario_id,[{'industry':'锂电','product_models':['AM600'],'trigger_conditions':'运行中异常掉电','business_impact':'产线状态丢失','recovery_method':'恢复参数','evidence_count':1}])
    saved=repository.scenario(scenario_id)
    assert saved['failure_mode']=='保持变量丢失' and saved['industry_variants'][0]['product_model_values']==['AM600']
    repository.create_generation('QSG-LEDGER','PLC','1月','12月',1,'TEST')
    repository.initialize_issue_classifications('QSG-LEDGER',[{'knowledge_id':'QK-X','business_issue_id':'ITR-X'}])
    repository.mark_issue_classification('QSG-LEDGER','QK-X','REVIEW_REQUIRED',error_message='需要人工确认')
    repository.refresh_generation_coverage('QSG-LEDGER')
    page=TestClient(create_app(db)).get('/quality-scenarios/generations/QSG-LEDGER/issues')
    assert page.status_code==200 and '问题识别覆盖明细' in page.text and 'ITR-X' in page.text and '重试选中问题' in page.text


def test_generation_requires_active_taxonomy_for_selected_product(tmp_path):
    repository=ScenarioRepository(tmp_path/'product-generation.db')
    service=ScenarioGenerationService(FakeIssues(),repository,tmp_path,FakeClient())
    import pytest
    with pytest.raises(ValueError,match='SCENARIO_PRODUCT_TAXONOMY_NOT_ACTIVE:HMI'):
        service.generate('HMI','1月','12月')
    draft=repository.create_draft('HMI','PLC');repository.activate(draft)
    result=service.generate('HMI','1月','12月')
    item=repository.scenario(result['scenario_ids'][0])
    assert item['product_code']=='HMI' and item['taxonomy_version_id']==draft
    assert repository.generation(result['generation_id'])['taxonomy_version_id']==draft
