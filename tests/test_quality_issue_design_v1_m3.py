import json
from pathlib import Path
from openpyxl import Workbook
from builder.ai_client import AIResponse
from quality_knowledge.repositories import IssueKnowledgeRepository
from quality_knowledge.services import KnowledgeIssueService

ROOT=Path(__file__).parents[1]
class FakeClient:
    def complete(self,messages):
        sys=messages[0]['content'];ref={'source_type':'FIELD','source_id':'PLC:ITR-1','field_path':'occurrence.root_cause'};ev={'value':'变更影响未覆盖','confidence':.9,'evidence_type':'INFERRED','reason':'字段证据','source_refs':[ref]}
        if '发生原因分析器' in sys:d={'root_cause_summary':ev,'failure_mechanism':ev,'contributing_factors':[ev],'occurrence_category':'变更引入','confidence':.9,'evidence':[ref]}
        elif '流出原因分析器' in sys:d={'escape_cause_summary':ev,'verification_gap':ev,'process_gap':ev,'escape_category':'变更影响分析','confidence':.9,'evidence':[ref]}
        elif '再发风险分析器' in sys:d={'recurrence_risk_level':'HIGH','recurrence_risk_reason':ev,'existing_control_coverage':ev,'residual_risk':ev,'is_common_issue':True,'potential_affected_products':['HMI'],'horizontal_action_needed':True}
        else:d={'capability_gaps':[{'gap_id':'g1','dimension':'MANAGEMENT','category':'CHANGE_MANAGEMENT','description':'缺少变更影响分析','why_needed':'防再发','related_mechanism':'变更漏覆盖','recommended_control':'建立机制','scope':'跨产品','affected_products':['PLC','HMI'],'confidence':.9,'evidence':[ref]}]}
        return AIResponse(json.dumps(d,ensure_ascii=False),'fake-m3',{})
def seed(tmp_path):
    f=tmp_path/'plc.xlsx';w=Workbook();s=w.active;s.append(['ITR单号','问题描述','问题原因定位','是否漏测','原因分类一级']);s.append(['ITR-1','异常','变更引入','是','测试']);w.save(f)
    repo=IssueKnowledgeRepository(tmp_path/'q.db');svc=KnowledgeIssueService(repo);r=svc.import_file(f,'PLC');kid=svc.query_issues({},10)[0]['knowledge_id'];return repo,svc,kid

def test_m3_analysis_current_version_and_trace(tmp_path):
    repo,svc,kid=seed(tmp_path);out=svc.run_issue_analysis(kid,ROOT,client=FakeClient());assert out['status']=='COMPLETED';assert len(repo.get_analysis_history(kid))==4;g=repo.list_capability_gaps(kid)[0];assert g['dimension']=='MANAGEMENT';assert g['issue_version_id']==repo.get_current_issue(kid)['current_version_id']
def test_m3_new_version_does_not_reuse_old_latest(tmp_path):
    repo,svc,kid=seed(tmp_path);svc.run_issue_analysis(kid,ROOT,client=FakeClient());old=repo.get_latest_analysis(kid,'occurrence');assert old
    # create V2 through repository path by re-importing changed workbook
    f=tmp_path/'plc2.xlsx';w=Workbook();s=w.active;s.append(['ITR单号','问题描述','问题原因定位','是否漏测']);s.append(['ITR-1','异常已变化','新根因','是']);w.save(f);svc.import_file(f,'PLC')
    assert repo.get_latest_analysis(kid,'occurrence') is None
