import json
from pathlib import Path
from openpyxl import Workbook
from builder.ai_client import AIResponse
from quality_knowledge.repositories import IssueKnowledgeRepository
from quality_knowledge.services import KnowledgeIssueService

ROOT=Path(__file__).parents[1]
class GapClient:
    def complete(self,messages):
        sys=messages[0]['content']
        if '发生原因分析器' in sys: d={'root_cause_summary':'变更影响遗漏','failure_mechanism':'异常路径未处理','confidence':.9}
        elif '流出原因分析器' in sys: d={'escape_cause_summary':'回归未覆盖','verification_gap':'缺少异常用例','process_gap':'变更影响未进入必测','confidence':.9}
        elif '再发风险分析器' in sys: d={'recurrence_risk_level':'HIGH','recurrence_risk_reason':'控制仍为单点','existing_control_coverage':'当前代码修复','residual_risk':'其他产品仍有风险','is_common_issue':True,'potential_affected_products':['PLC','HMI'],'horizontal_action_needed':True}
        else: d={'capability_gaps':[{'dimension':'MANAGEMENT','category':'CHANGE_MANAGEMENT','description':'缺少变更影响分析能力','recommended_action':'建立公共变更影响分析规范和Checklist','action_type':'MANAGEMENT_MECHANISM','action_target':'变更影响分析机制','expected_prevention_effect':'降低同类变更遗漏再次流出的风险','confidence':.9,'evidence':[]}]}
        return AIResponse(json.dumps(d,ensure_ascii=False),'gap-model',{})

def book(path, headers, row):
    w=Workbook();s=w.active;s.append(headers);s.append(row);w.save(path)

def test_gap01_04_mapping_coverage(tmp_path):
    f=tmp_path/'plc.xlsx';book(f,['ITR 单号','问题描述','问题原因定位（×开发填写×）','横向影响域','自定义未知字段'],['I-1','异常','根因','PLC/HMI','x'])
    repo=IssueKnowledgeRepository(tmp_path/'q.db');svc=KnowledgeIssueService(repo);r=svc.import_file(f,'PLC')
    m=r['diagnostics']['processed_sheets'][0]['mapping_coverage']
    assert m['total_source_fields']==5 and m['matched_structured']>=3 and m['matched_extension']>=1
    assert m['raw_only']==1 and m['unmatched']==1 and '自定义未知字段' in m['unmatched_fields']
    assert any(x['source_header']=='ITR 单号' and x['target_field']=='identity.business_issue_id' for x in m['fields'])

def test_gap02_prevention_action_persisted(tmp_path):
    f=tmp_path/'plc.xlsx';book(f,['ITR单号','问题描述','问题原因定位'],['I-2','异常','根因'])
    repo=IssueKnowledgeRepository(tmp_path/'q.db');svc=KnowledgeIssueService(repo);svc.import_file(f,'PLC');kid=svc.query_issues({},10)[0]['knowledge_id']
    svc.run_issue_analysis(kid,ROOT,client=GapClient());g=svc.query_capability_gaps(kid)[0]
    assert g['recommended_action'].startswith('建立公共') and g['action_target']=='变更影响分析机制'
    assert '降低同类' in g['expected_prevention_effect']

def test_gap03_cross_product_common_gap(tmp_path):
    repo=IssueKnowledgeRepository(tmp_path/'q.db');svc=KnowledgeIssueService(repo)
    p=tmp_path/'p.xlsx';book(p,['ITR单号','问题描述','问题原因定位'],['P-1','异常','根因']);svc.import_file(p,'PLC')
    h=tmp_path/'h.xlsx';book(h,['TRC单号','问题描述','根因分析'],['H-1','异常','根因']);svc.import_file(h,'HMI')
    for x in svc.query_issues({},10): svc.run_issue_analysis(x['knowledge_id'],ROOT,client=GapClient())
    rows=svc.get_common_capability_gaps(min_issues=2)
    hit=next(x for x in rows if x['category']=='CHANGE_MANAGEMENT')
    assert hit['related_issue_count']==2 and hit['business_type_count']==2
    assert 'PLC' in hit['business_types'] and 'HMI' in hit['business_types']
