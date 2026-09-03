import json

from fastapi.testclient import TestClient

from builder.ai_client import AIResponse
from quality_knowledge.scenario_generation import ScenarioGenerationService
from quality_knowledge.scenarios import ScenarioRepository
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
    assert service.precheck('PLC','1月','12月')=={'issue_count':2,'analysed_count':2,'ready':True,'coverage_rate':100.0}


def test_generation_page_is_not_swallowed_by_scenario_detail_route(tmp_path):
    client=TestClient(create_app(tmp_path/'web.db'))
    page=client.get('/quality-scenarios/generate')
    assert page.status_code==200
    assert 'AI生成质量场景候选' in page.text and '最近生成记录' in page.text
    assert '/quality-scenarios/generate' in client.get('/quality-scenarios').text
