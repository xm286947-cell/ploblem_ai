import json
from pathlib import Path
from openpyxl import Workbook
from builder.ai_client import AIResponse
from quality_knowledge.repositories import IssueKnowledgeRepository
from quality_knowledge.services import KnowledgeIssueService

ROOT=Path(__file__).parents[1]

class SimpleShapeClient:
    def complete(self,messages):
        sys=messages[0]['content']
        if '发生原因分析器' in sys:
            data={'root_cause_summary':'版本变更引入逻辑错误','failure_mechanism':'异常输入触发错误分支','contributing_factors':['变更影响分析不足'],'occurrence_category':'CHANGE','confidence':0.88,'evidence':[]}
        elif '流出原因分析器' in sys:
            data={'escape_cause_summary':'测试未覆盖变更路径','verification_gap':'缺少对应场景用例','process_gap':'变更影响分析未闭环','escape_category':'TEST_GAP','confidence':0.82,'evidence':[]}
        elif '再发风险分析器' in sys:
            data={'recurrence_risk_level':'HIGH','recurrence_risk_reason':'当前措施偏单点修复','existing_control_coverage':'仅修复当前代码','residual_risk':'同类路径仍可能遗漏','is_common_issue':True,'potential_affected_products':'HMI','horizontal_action_needed':True}
        else:
            data={'capability_gaps':[{'gap_dimension':'MANAGEMENT','gap_category':'CHANGE_MANAGEMENT','gap_description':'缺少变更影响闭环','recommended_control':'建立变更影响检查清单','confidence':0.8,'evidence_refs':[]}]}
        return AIResponse(json.dumps(data,ensure_ascii=False),'simple-model',{})

class InvalidClient:
    def complete(self,messages):
        return AIResponse('{"root_cause_summary":', 'bad-model', {})

def seed(tmp_path):
    f=tmp_path/'plc.xlsx'; w=Workbook(); s=w.active
    s.append(['ITR 单号','问题描述','问题原因定位（×开发填写×）','是否漏测'])
    s.append(['ITR-RC3-1','异常','变更引入','是']); w.save(f)
    repo=IssueKnowledgeRepository(tmp_path/'q.db'); svc=KnowledgeIssueService(repo)
    result=svc.import_file(f,'PLC'); assert result['new']==1
    kid=svc.query_issues({},10)[0]['knowledge_id']
    return repo,svc,kid

def test_simple_llm_shape_is_normalized_and_persisted(tmp_path):
    repo,svc,kid=seed(tmp_path)
    out=svc.run_issue_analysis(kid,ROOT,client=SimpleShapeClient())
    assert out['status']=='COMPLETED'
    occ=repo.get_latest_analysis(kid,'occurrence')['result']
    assert occ['root_cause_summary']['value']=='版本变更引入逻辑错误'
    assert occ['contributing_factors'][0]['value']=='变更影响分析不足'
    rec=repo.get_latest_analysis(kid,'recurrence')['result']
    assert rec['potential_affected_products']==['HMI']
    gap=repo.list_capability_gaps(kid)[0]
    assert gap['dimension']=='MANAGEMENT'
    # prove persisted after repository restart
    repo2=IssueKnowledgeRepository(tmp_path/'q.db')
    assert repo2.get_latest_analysis(kid,'occurrence')['result']['root_cause_summary']['value']=='版本变更引入逻辑错误'
    run=repo2.get_analysis_history(kid)[0]
    assert repo2.get_analysis_debug(run['analysis_run_id']) is not None

def test_failed_parse_keeps_raw_response_and_validation_error(tmp_path):
    repo,svc,kid=seed(tmp_path)
    out=svc.run_issue_analysis(kid,ROOT,client=InvalidClient())
    assert out['status']=='FAILED'
    run=repo.get_analysis_history(kid)[0]
    assert run['status']=='FAILED'
    dbg=repo.get_analysis_debug(run['analysis_run_id'])
    assert dbg is not None
    assert dbg['raw_response'] is not None
    assert dbg['validation_error']
