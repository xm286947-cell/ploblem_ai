import json, os
from pathlib import Path
from builder.ai_client import OpenAICompatibleClient, AIResponse, AIClientError
from quality_knowledge.analyzers import RecurrenceAnalyzer, CapabilityGapAnalyzer

ROOT=Path(__file__).parents[1]

class CaptureClient:
    def __init__(self,response): self.response=response
    def complete(self,messages): return self.response

def test_stage_token_budget_loaded():
    rec=RecurrenceAnalyzer(ROOT,client=CaptureClient(None))
    gap=CapabilityGapAnalyzer(ROOT,client=CaptureClient(None))
    assert rec.ai_cfg['max_tokens']==2048
    assert gap.ai_cfg['max_tokens']==4096
    assert gap.ai_cfg['max_items_per_dimension']==3

def test_capability_gap_top3_per_dimension():
    ref={'source_type':'FIELD','source_id':'X','field_path':'x'}
    gaps=[]
    for dim in ('TECHNICAL','MANAGEMENT','GOVERNANCE'):
        for i in range(5):
            gaps.append({'gap_id':f'{dim}-{i}','dimension':dim,'category':'TEST_CAPABILITY' if dim=='TECHNICAL' else ('PROCESS' if dim=='MANAGEMENT' else 'COMMON_STANDARD'),'description':f'g{i}','why_needed':'need','related_mechanism':'m','recommended_control':'c','recommended_action':'a','action_type':'BUILD','action_target':'t','expected_prevention_effect':'e','scope':'s','affected_products':[],'confidence':.9,'evidence':[ref]})
    response=AIResponse(json.dumps({'capability_gaps':gaps},ensure_ascii=False),'m',{'choices':[{'finish_reason':'stop'}]})
    a=CapabilityGapAnalyzer(ROOT,client=CaptureClient(response))
    result,_,debug=a.analyze({'issue':{'x':'y'}})
    assert len(result)==9
    assert debug['max_tokens']==4096
    assert debug['finish_reason']=='stop'
    assert debug['response_truncated'] is False

def test_debug_records_output_diagnostics():
    ref={'source_type':'FIELD','source_id':'X','field_path':'x'}
    ev={'value':'短结论','confidence':.9,'evidence_type':'INFERRED','reason':'r','source_refs':[ref]}
    body={'recurrence_risk_level':'LOW','recurrence_risk_reason':ev,'existing_control_coverage':ev,'residual_risk':ev,'is_common_issue':False,'potential_affected_products':[],'horizontal_action_needed':False}
    raw={'choices':[{'finish_reason':'stop'}],'usage':{'prompt_tokens':100,'completion_tokens':50}}
    a=RecurrenceAnalyzer(ROOT,client=CaptureClient(AIResponse(json.dumps(body,ensure_ascii=False),'qwen',raw)))
    _,_,debug=a.analyze({'issue':{'a':'b'}})
    assert debug['input_chars']>0 and debug['output_chars']>0
    assert debug['usage']['completion_tokens']==50
    assert debug['max_tokens']==2048
