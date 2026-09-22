import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from quality_knowledge.web.app import create_app
from quality_knowledge.scenarios import ScenarioRepository
from quality_knowledge.scenario_generation import ScenarioGenerationService
from quality_knowledge.reverse_quality import _analysis_review_status


class FakeResponse:
    model = 'test-model'
    def __init__(self, content):self.content=json.dumps(content,ensure_ascii=False)


class FakeClient:
    def complete(self, messages):
        return FakeResponse({'fields':{
            'customer_experience':{'value':'掉电后关键计数丢失','evidence_ids':['cs.description'],'confidence':.9},
            'expected_quality_state':{'value':'重新上电后计数应正确恢复','evidence_ids':['cs.description'],'confidence':.8},
            'preconditions':{'value':'PLC 正常运行时','evidence_ids':['cs.description'],'confidence':.9},
            'root_cause':{'value':'保持变量写入未完成','evidence_ids':['cs.root_cause'],'confidence':1},
            'recovery_method':{'value':'重新上电恢复运行','evidence_ids':['structured.recovery_measure'],'confidence':.95},
            'related_objects':{'value':'PLC AM600','evidence_ids':['structured.product_model'],'confidence':.9},
            'quality_requirement_candidate':{'value':'异常掉电后关键运行数据能够正确恢复','evidence_ids':['cs.description','cs.root_cause'],'confidence':.75},
            'lifecycle_stage':{'value':'运行执行','evidence_ids':['cs.description','cs.phase'],'confidence':.85},
            'business_activity_scene':{'value':'掉电数据保持与上电恢复','evidence_ids':['cs.description'],'confidence':.9},
        },'lifecycle_code':'RUNTIME_EXECUTION','activity_code':'POWER_LOSS_RETENTION_RECOVERY',
        'match_reason':'有掉电和重新上电的数据恢复证据','missing_condition':'系统规模未知',
        'questions':[{'field_name':'scale_or_load','reason':'原始问题未给出系统规模',
                      'question':'现场参与设备规模是多少？','evidence_needed':['现场拓扑或设备数量']} ]})


def setup_case(tmp_path):
    app=create_app(tmp_path/'reverse.db')
    repo=app.state.material_repository
    material_id,_=repo.add_material(repo.group('ITR-CS'),'ITR20260918001CS',{
        '问题信息_问题描述':'PLC 正常运行时异常掉电，重新上电后关键计数丢失',
        '问题信息_问题原因定位':'保持变量写入未完成',
        '问题信息_问题发生阶段':'终端正常使用',
        '问题信息_产品型号':'PLC AM600',
        '问题信息_问题领域':'软件',
        '问题信息_客户行业':'锂电',
        '问题信息_客户分级':'A',
        '问题信息_问题发生地点':'客户现场',
        '问题信息_问题发生地区归属':'华东',
        '问题信息_已用时长':'6个月',
        '问题信息_故障台数':'12',
        '问题信息_不良问题频率':'3次/周',
        '问题处理结果_问题解决方案':'重新上电恢复运行',
        '技术根因分析与纠正_软件模块':'RetainManager',
        '技术根因分析与纠正_软件功能':'掉电保持',
    },'synthetic.xlsx','Sheet1',2)
    app.state.reverse_quality_service.ai_client=FakeClient()
    return app,material_id


def test_reverse_quality_single_issue_analysis_review_and_source_preservation(tmp_path):
    app,material_id=setup_case(tmp_path)
    client=TestClient(app)
    page=client.get(f'/reverse-quality/{material_id}')
    assert page.status_code==200 and '单问题逆向质量分析' in page.text
    response=client.post(f'/reverse-quality/{material_id}/analyse',data={'product_code':'PLC'},follow_redirects=False)
    assert response.status_code==303
    service=app.state.reverse_quality_service
    saved=service.get('ITR20260918001')
    assert saved['status']=='PENDING_REVIEW'
    assert saved['review']['root_cause']['source_type']=='FACT'
    assert saved['review']['lifecycle_stage']['value']=='运行执行'
    assert saved['review']['business_activity_scene']['value']=='掉电数据保持与上电恢复'
    assert saved['review']['preconditions']['value']=='PLC 正常运行时'
    assert saved['review']['preconditions']['evidence_ids']==['cs.description']
    assert saved['review']['recovery_method']['value']=='重新上电恢复运行'
    assert saved['review']['recovery_method']['evidence_ids']==['structured.recovery_measure']
    assert saved['scene_match_status']=='NEED_REVIEW'
    assert len(saved['missing_information'])==1
    missing=saved['missing_information'][0]
    assert missing['field_name']=='scale_or_load'
    assert missing['question']=='现场参与设备规模是多少？'
    assert missing['evidence_needed']==['现场拓扑或设备数量']
    page=client.get(f'/reverse-quality/{material_id}')
    assert page.status_code==200
    assert '现场参与设备规模是多少？' in page.text
    resolved=client.post(f'/reverse-quality/{material_id}/missing-information',data={
        'missing_id':missing['missing_id'],'status':'CONFIRMED','answer':'现场共 12 台设备','reviewer':'质量专家'
    },follow_redirects=False)
    assert resolved.status_code==303
    saved=service.get('ITR20260918001')
    assert saved['missing_information'][0]['status']=='CONFIRMED'
    assert saved['missing_information'][0]['answer']=='现场共 12 台设备'
    assert saved['missing_information'][0]['reviewer']=='质量专家'
    with service.repository.connect() as connection:
        audit=connection.execute(
            "SELECT target_type,action,reviewer FROM reverse_quality_human_review "
            "WHERE run_id=? ORDER BY review_id DESC LIMIT 1",(saved['run_id'],)
        ).fetchone()
    assert dict(audit)=={'target_type':'MISSING_INFORMATION','action':'CONFIRMED','reviewer':'质量专家'}
    reviewed=client.post(f'/reverse-quality/{material_id}/review',data={'field_name':'expected_quality_state','action':'EDITED',
        'value':'重新上电后关键计数应保持一致','reviewer':'质量专家'},follow_redirects=False)
    assert reviewed.status_code==303
    saved=service.get('ITR20260918001')
    assert saved['ai']['expected_quality_state']['value']=='重新上电后计数应正确恢复'
    assert saved['review']['expected_quality_state']['value']=='重新上电后关键计数应保持一致'
    assert saved['review']['expected_quality_state']['review_status']=='CONFIRMED'
    with pytest.raises(ValueError,match='人工逐字段审核'):
        service.analyse(material_id,'PLC',force=True)


def test_reverse_quality_rejects_fabricated_root_cause(tmp_path):
    app,material_id=setup_case(tmp_path)
    class BadClient:
        def complete(self, messages):
            return FakeResponse({'fields':{'root_cause':{'value':'没有证据的新根因','evidence_ids':['cs.root_cause']}},
                                 'lifecycle_code':'RUNTIME_EXECUTION','activity_code':'POWER_LOSS_RETENTION_RECOVERY'})
    service=app.state.reverse_quality_service
    service.ai_client=BadClient()
    with pytest.raises(ValueError,match='已确认根因必须直接来自'):
        service.analyse(material_id,'PLC')
    assert service.get('ITR20260918001') is None


def test_reverse_quality_match_review_and_invalid_match(tmp_path):
    app,material_id=setup_case(tmp_path)
    service=app.state.reverse_quality_service
    service.analyse(material_id,'PLC')
    client=TestClient(app)
    response=client.post(f'/reverse-quality/{material_id}/match',data={
        'status':'NOT_MATCHED','scene_id':'','reason':'现有质量场景尚未覆盖该掉电保持条件',
        'missing_condition':'缺少异常掉电后的恢复要求','reviewer':'质量专家'},follow_redirects=False)
    assert response.status_code==303
    saved=service.get('ITR20260918001')
    assert saved['scene_match_status']=='NOT_MATCHED'
    assert saved['missing_condition']=='缺少异常掉电后的恢复要求'
    with pytest.raises(ValueError,match='必须选择场景'):
        service.review_match('ITR20260918001',status='MATCHED',scene_id='',reason='已覆盖',missing_condition='',reviewer='质量专家')


def test_reverse_quality_rejects_unreferenced_output_and_hardware(tmp_path):
    app,material_id=setup_case(tmp_path)
    service=app.state.reverse_quality_service
    class Unreferenced:
        def complete(self,messages):
            return FakeResponse({'fields':{'capability_gap':{'value':'随意编造的能力短板','evidence_ids':[]}},
                                 'lifecycle_code':'RUNTIME_EXECUTION','activity_code':''})
    service.ai_client=Unreferenced()
    with pytest.raises(ValueError,match='缺少可核验的来源证据'):
        service.analyse(material_id,'PLC')
    hardware_id,_=app.state.material_repository.add_material(app.state.material_repository.group('ITR-CS'),
        'ITR20260918002CS',{'问题信息_问题描述':'电源板器件损坏','问题信息_问题领域':'硬件'},
        'synthetic.xlsx','Sheet1',3)
    with pytest.raises(ValueError,match='仅处理软件问题'):
        service.analyse(hardware_id,'PLC')

def test_reverse_quality_facts_include_v01_context_evidence(tmp_path):
    app,material_id=setup_case(tmp_path)
    facts=app.state.reverse_quality_service.facts(material_id)
    expected={
        'structured.customer_industry':'锂电',
        'structured.customer_level':'A',
        'structured.occurrence_location':'客户现场',
        'structured.occurrence_region':'华东',
        'structured.used_duration':'6个月',
        'structured.failure_count':'12',
        'structured.failure_frequency':'3次/周',
        'structured.recovery_measure':'重新上电恢复运行',
        'structured.software_module':'RetainManager',
        'structured.software_function':'掉电保持',
    }
    assert {key:facts['evidence'][key]['value'] for key in expected}==expected

def test_reverse_quality_string_questions_are_persisted_backward_compatibly(tmp_path):
    app,material_id=setup_case(tmp_path)
    class StringQuestionClient(FakeClient):
        def complete(self,messages):
            response=super().complete(messages)
            payload=json.loads(response.content)
            payload['questions']=['请确认现场系统规模']
            return FakeResponse(payload)
    service=app.state.reverse_quality_service
    service.ai_client=StringQuestionClient()
    service.analyse(material_id,'PLC')
    missing=service.get('ITR20260918001')['missing_information']
    assert len(missing)==1
    assert missing[0]['field_name']==''
    assert missing[0]['question']=='请确认现场系统规模'
    assert missing[0]['status']=='PENDING'


def test_reverse_quality_missing_information_confirmation_requires_answer(tmp_path):
    app,material_id=setup_case(tmp_path)
    service=app.state.reverse_quality_service
    service.analyse(material_id,'PLC')
    missing=service.get('ITR20260918001')['missing_information'][0]
    with pytest.raises(ValueError,match='请填写人工答案'):
        service.review_missing_information(
            'ITR20260918001',missing_id=missing['missing_id'],
            status='CONFIRMED',answer='',reviewer='质量专家')

def test_reverse_quality_result_and_candidate_api_use_single_analysis_pass(tmp_path):
    app,material_id=setup_case(tmp_path)
    class CountingClient(FakeClient):
        def __init__(self):
            self.calls=0
        def complete(self,messages):
            self.calls+=1
            return super().complete(messages)

    ai=CountingClient()
    service=app.state.reverse_quality_service
    service.ai_client=ai
    service.analyse(material_id,'PLC')
    assert ai.calls==1

    client=TestClient(app)
    result_response=client.get('/api/reverse-quality/ITR20260918001')
    assert result_response.status_code==200
    result=result_response.json()
    assert result['result_version']=='reverse-quality-v0.1'
    assert result['identity']['canonical_itr']=='ITR20260918001'
    assert result['fields']['preconditions']['value']=='PLC 正常运行时'
    assert result['fields']['recovery_method']['value']=='重新上电恢复运行'

    candidate_response=client.get('/api/reverse-quality/ITR20260918001/scenario-candidate')
    assert candidate_response.status_code==200
    adapted=candidate_response.json()
    assert adapted['adapter_version']=='reverse-quality-scenario-adapter-v0.1'
    assert adapted['source_result_version']=='reverse-quality-v0.1'
    assert adapted['ready'] is True
    assert adapted['blockers']==[]
    candidate=adapted['candidate']
    assert candidate['lifecycle_code']=='RUNTIME_EXECUTION'
    assert candidate['activity_code']=='POWER_LOSS_RETENTION_RECOVERY'
    assert candidate['preconditions']=='PLC 正常运行时'
    assert candidate['recovery_method']=='重新上电恢复运行'
    assert candidate['participating_systems']=='PLC AM600'
    assert candidate['confirmation_questions']==['现场参与设备规模是多少？']
    assert adapted['field_evidence']['preconditions']['reverse_field']=='preconditions'
    assert adapted['field_evidence']['recovery_method']['evidence_ids']==['structured.recovery_measure']
    assert ai.calls==1


def test_reverse_quality_candidate_rejects_unknown_result_version(tmp_path):
    repository=ScenarioRepository(tmp_path/'scenario.db')
    service=ScenarioGenerationService(None,repository,tmp_path)
    bad={
        'result_version':'reverse-quality-v9',
        'analysis_id':'A1',
        'run_id':'R1',
        'identity':{'canonical_itr':'ITR-X','product_code':'PLC','taxonomy_version_id':repository.taxonomy_active('PLC')['version_id']},
        'fields':{},
        'missing_information':[],
        'scene_match':{},
    }
    with pytest.raises(ValueError,match='REVERSE_QUALITY_RESULT_VERSION_UNSUPPORTED'):
        service.candidate_from_reverse_quality(bad)

def test_reverse_quality_confirmation_gate_requires_missing_information_resolution():
    confirmed={name:{
        'value':'x','source_type':'FACT','evidence_ids':['e1'],
        'confidence':1.0,'review_status':'CONFIRMED','reviewer_edit':''
    } for name in (
        'customer_task','customer_experience','expected_quality_state',
        'lifecycle_stage','business_activity_scene','failure_mode',
        'capability_gap','quality_requirement_candidate'
    )}
    item={
        'review':confirmed,
        'match_reviewed':True,
        'scene_match_status':'NOT_MATCHED',
        'missing_information':[{
            'missing_id':'M1','status':'PENDING','question':'请确认规模'
        }],
    }
    assert _analysis_review_status(item)=='IN_REVIEW'
    item['missing_information'][0]['status']='CONFIRMED'
    assert _analysis_review_status(item)=='CONFIRMED'
    item['missing_information'][0]['status']='NOT_APPLICABLE'
    assert _analysis_review_status(item)=='CONFIRMED'

def test_reverse_quality_default_business_path_uses_runtime_executor(tmp_path):
    app,material_id=setup_case(tmp_path)
    service=app.state.reverse_quality_service
    service.ai_client=None

    class FakeRuntimeExecutor:
        def __init__(self):
            self.calls=[]
        def execute(self,payload,*,request_id):
            self.calls.append((payload,request_id))
            fake=json.loads(FakeClient().complete([]).content)
            return SimpleNamespace(data=fake,model='runtime-test-model')

    runtime=FakeRuntimeExecutor()
    service._runtime_executor=runtime
    saved=service.analyse(material_id,'PLC')
    assert len(runtime.calls)==1
    payload,request_id=runtime.calls[0]
    assert payload['facts']['canonical_itr']=='ITR20260918001'
    assert payload['field_names']
    assert request_id.startswith('reverse-quality:ITR20260918001:')
    assert saved['result']['model']=='runtime-test-model'


def test_reverse_quality_runtime_adoption_has_no_business_provider_or_retry_path():
    root=Path(__file__).resolve().parents[1]
    service=(root/'quality_knowledge'/'reverse_quality.py').read_text(encoding='utf-8')
    integration=(root/'quality_knowledge'/'reverse_quality_runtime.py').read_text(encoding='utf-8')
    agent=(root/'config'/'runtime'/'agents'/'reverse_quality.single_issue.analyze.yaml').read_text(encoding='utf-8')

    assert 'OpenAICompatibleClient' not in service
    assert 'load_quality_issue_ai_config' not in service
    assert 'OpenAICompatibleClient' not in integration
    assert 'urllib' not in integration
    assert 'requests' not in integration
    assert 'httpx' not in integration
    assert integration.count('AgentConfigLoader(')==1
    assert 'ConfiguredAgentRuntime(' in integration
    assert 'model_ref: qwen_prod' in agent
    assert 'base_url:' not in agent
    assert 'api_key:' not in agent

