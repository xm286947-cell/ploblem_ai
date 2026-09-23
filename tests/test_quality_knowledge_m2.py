import json
from pathlib import Path
from builder.ai_client import AIResponse
from quality_knowledge.repositories import SqliteIssueKnowledgeRepository
from quality_knowledge.services import QualityIssueAnalysisService
from quality_knowledge.models.issue import *

class FakeClient:
    def complete(self,messages):
        sys=messages[0]['content']
        ref={"source_type":"FIELD","source_id":"PLC:1","field_path":"occurrence_root_cause"}
        ev={"value":"x","confidence":0.8,"evidence_type":"INFERRED","reason":"r","source_refs":[ref]}
        if '发生原因' in sys: d={"why_occurred":ev,"root_cause":ev,"failure_mechanism":ev,"contributing_factors":[ev]}
        elif '流出原因' in sys: d={"why_escaped":ev,"verification_gap":ev,"process_gap":ev,"escape_mechanism":ev}
        elif '再发风险' in sys: d={"recurrence_risk_level":"HIGH","recurrence_risk_reason":ev,"existing_control_coverage":ev,"residual_risk":ev}
        else: d={"capability_gaps":[{"gap_dimension":"MANAGEMENT","gap_category":"CHANGE_MANAGEMENT","gap_description":"缺少变更影响分析","why_needed":"防再发","related_issue_mechanism":"变更漏覆盖","recommended_control":"建立变更影响分析","scope":"PLC","confidence":0.9,"evidence_refs":[ref]}]}
        return AIResponse(json.dumps(d,ensure_ascii=False),'fake-model',{})

def seed(repo):
    q=QualityIssueDTO(identity=IssueIdentity(knowledge_id='k1',case_id='c1',business_type='PLC',issue_id='1',source_record_id='r1'),source=IssueSource(source_hash='h1'),issue_fact=IssueFact(description='问题'),product_context=ProductContext(),occurrence=OccurrenceFact(root_cause_original='变更引入'),escape=EscapeFact(is_escape='是',root_cause_original='影响分析不足'),solution=SolutionFact(improvement_action='补用例'),raw_record={'备注':'secret'})
    repo.save(q)

def test_four_stage_analysis_and_trace(tmp_path):
    repo=SqliteIssueKnowledgeRepository(tmp_path/'q.db'); seed(repo)
    svc=QualityIssueAnalysisService(repo,Path(__file__).parents[1],client=FakeClient())
    out=svc.analyze_one('k1')
    assert out['status']=='SUCCESS'
    assert set(out['stages'])=={'occurrence','escape','recurrence','capability_gap'}
    with repo.connect() as c:
        assert c.execute("select count(*) from analysis_run where status='SUCCESS'").fetchone()[0]==4
        gap=c.execute('select gap_dimension,gap_category from issue_capability_gap').fetchone()
        assert tuple(gap)==('MANAGEMENT','CHANGE_MANAGEMENT')

def test_failure_does_not_remove_previous_success(tmp_path):
    repo=SqliteIssueKnowledgeRepository(tmp_path/'q.db'); seed(repo)
    svc=QualityIssueAnalysisService(repo,Path(__file__).parents[1],client=FakeClient()); svc.analyze_one('k1')
    before=repo.latest_analysis('k1','occurrence')
    class Bad:
        def complete(self,messages): raise RuntimeError('boom')
    bad=QualityIssueAnalysisService(repo,Path(__file__).parents[1],client=Bad())
    out=bad.analyze_one('k1',overwrite=True)
    assert out['status']=='PARTIAL_FAILED'
    assert repo.latest_analysis('k1','occurrence')['analysis_run_id']==before['analysis_run_id']

def test_customer_is_minimized_by_default(tmp_path):
    repo=SqliteIssueKnowledgeRepository(tmp_path/'q.db'); seed(repo)
    with repo.connect() as c: c.execute("update issue_record set customer='ACME' where knowledge_id='k1'")
    svc=QualityIssueAnalysisService(repo,Path(__file__).parents[1],client=FakeClient())
    assert svc._context('k1')['customer']==''
