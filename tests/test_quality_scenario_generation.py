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
            "experience_requirement":"在线调试持续流畅","concern_points":"卡顿、响应慢","quality_attribute":"性能效率",
            "applicable_boundary":"大型工程、大量变量、持续监控","validation_direction":"验证P95响应时间和长稳退化",
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


def test_generation_precheck_requires_existing_analysis(tmp_path):
    service=ScenarioGenerationService(FakeIssues(),ScenarioRepository(tmp_path/'scenario.db'),tmp_path,FakeClient())
    check=service.precheck('PLC','1月','12月')
    assert {key:check[key] for key in ('issue_count','analysed_count','ready','coverage_rate')}=={'issue_count':2,'analysed_count':2,'ready':True,'coverage_rate':100.0}
    assert len(check['items'])==2


def test_generation_page_is_not_swallowed_by_scenario_detail_route(tmp_path):
    client=TestClient(create_app(tmp_path/'web.db'))
    page=client.get('/quality-scenarios/generate')
    assert page.status_code==200
    assert 'AI生成质量场景候选' in page.text and '最近生成记录' in page.text
    assert '/quality-scenarios/generate' in client.get('/quality-scenarios').text


def test_selected_issue_scope_and_itr_cs_context_are_used(tmp_path):
    db=tmp_path/'scenario.db';repository=ScenarioRepository(db);MaterialRepository(db)
    raw={'问题信息_IPMT':'控制IPMT','问题信息_SPDT':'PLC SPDT','问题信息_产品型号':'AM600','技术根因分析与纠正_TRC纠正信息':'修复刷新调度'}
    with repository.connect() as c:
        c.execute("INSERT INTO source_material(material_id,group_id,material_type,business_key,canonical_itr,version_no,source_hash,raw_json) VALUES('MAT-CS','DG-ITR-CS','ITR_CS','ITR001CS','ITR001',1,'H',?)",(json.dumps(raw,ensure_ascii=False),))
        c.execute("INSERT INTO issue_material_link(link_id,knowledge_id,material_id,link_status) VALUES('L1','QK-1','MAT-CS','LINKED')")
    service=ScenarioGenerationService(FakeIssues(),repository,tmp_path,FakeClient())
    records=service._records('PLC','1月','12月',['QK-1'])
    assert len(records)==1 and records[0]['itr_cs_context']['trc_correction']=='修复刷新调度'
    result=service.generate('PLC','1月','12月',selected_ids=['QK-1'])
    item=repository.scenario(result['scenario_ids'][0])
    assert item['scopes']=={'IPMT':['控制IPMT'],'SPDT':['PLC SPDT'],'PRODUCT_MODEL':['AM600']}
    assert [x['knowledge_id'] for x in item['evidence']]==['QK-1']


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


def test_generation_status_endpoint_exposes_progress_and_failure(tmp_path):
    client=TestClient(create_app(tmp_path/'web.db'));repository=client.app.state.scenario_repository
    repository.create_generation('QSG-STATUS','PLC','1月','12月',10,'TEST')
    repository.update_generation('QSG-STATUS',status='FAILED',progress_text='生成失败',error_message='模型响应格式错误')
    status=client.get('/api/quality-scenario-generations/QSG-STATUS').json()
    assert status['status']=='FAILED' and status['error_message']=='模型响应格式错误'
    page=client.get('/quality-scenarios/generate?job_id=QSG-STATUS')
    assert 'scenario-job-status' in page.text and '状态查询失败' in page.text
