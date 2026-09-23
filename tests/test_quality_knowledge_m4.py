from repositories import JsonArtifactRepository
from services import KnowledgeService
from quality_knowledge.repositories.sqlite_repository import SqliteIssueKnowledgeRepository
from quality_knowledge.models.issue import *


def _issue(k='K1'):
    return QualityIssueDTO(
      identity=IssueIdentity(knowledge_id=k,case_id=k,business_type='PLC',issue_id='ITR-1',source_record_id='1'),
      source=IssueSource(source_file='x.xlsx',source_sheet='S',source_row=2,source_import_batch='B',raw_record_ref='r',source_hash='h1'),
      issue_fact=IssueFact(title='掉电后数据丢失',description='掉电保持数据异常',product='PLC',platform='P1',department='D'),
      product_context=ProductContext(product='PLC',platform='P1',module='Runtime',feature_l1='保持'),
      occurrence=OccurrenceFact(root_cause_original='保持区写入逻辑缺陷',cause_l1='软件设计',cause_l2='状态管理'),
      escape=EscapeFact(is_escape='是',root_cause_original='缺少掉电场景测试',escape_l1='测试覆盖',escape_l2='场景缺失'),
      solution=SolutionFact(original_solution='修复逻辑',technical_action='封装统一接口',management_action='增加必测项'),
      verification=VerificationFact(), product_extension={}, raw_record={'ITR':'ITR-1'})


def test_publish_quality_knowledge_into_existing_retrieval_contract(tmp_path):
    qr=SqliteIssueKnowledgeRepository(tmp_path/'q.db'); qr.save(_issue())
    js=JsonArtifactRepository(tmp_path)
    service=KnowledgeService.with_quality_repository(js,qr)
    result=service.publish_issue_to_retrieval('K1')
    assert result['status']=='PUBLISHED'
    artifacts=service.load_case_artifacts('QK-K1')
    assert artifacts.retrieval_document['filters']['knowledge_source']=='QUALITY_ISSUE_SQLITE'
    assert artifacts.enriched_case['metadata']['quality_knowledge_id']=='K1'
    assert '掉电保持数据异常' in artifacts.retrieval_document['text']


def test_publish_is_idempotent_and_old_contract_stays_available(tmp_path):
    qr=SqliteIssueKnowledgeRepository(tmp_path/'q.db'); qr.save(_issue())
    js=JsonArtifactRepository(tmp_path); js.save('knowledge/retrieval_docs/OLD.json',{'case_id':'OLD','source_case_path':''})
    service=KnowledgeService.with_quality_repository(js,qr)
    assert service.publish_issue_to_retrieval('K1')['status']=='PUBLISHED'
    assert service.publish_issue_to_retrieval('K1')['status']=='SKIPPED'
    assert service.repository.load('knowledge/retrieval_docs/OLD.json')['case_id']=='OLD'
