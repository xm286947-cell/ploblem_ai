from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from typing import Any


def _t(v: Any) -> str:
    return '' if v is None else str(v).strip()


def _dedupe(values):
    out=[]
    for v in values:
        s=_t(v)
        if s and s not in out: out.append(s)
    return out


class QualityKnowledgeRetrievalAdapter:
    """Maps Quality Issue Knowledge to the existing Repeat Case retrieval contract.

    This is intentionally an adapter: it does not alter Similarity/Solution/Repeat Decision logic.
    """
    def __init__(self, issue_repository):
        self.repository=issue_repository

    def build(self, knowledge_id: str) -> tuple[dict[str,Any], dict[str,Any]]:
        rows=self.repository.query({'knowledge_id': knowledge_id}, limit=1)
        if not rows:
            raise KeyError(f'Quality knowledge not found: {knowledge_id}')
        r=rows[0]
        occurrence=self.repository.latest_analysis(knowledge_id,'occurrence')
        escape=self.repository.latest_analysis(knowledge_id,'escape')
        recurrence=self.repository.latest_analysis(knowledge_id,'recurrence')
        gaps=self.repository.query_capability_gaps({'knowledge_id':knowledge_id},limit=100)

        occ=(occurrence or {}).get('result') or {}
        esc=(escape or {}).get('result') or {}
        rec=(recurrence or {}).get('result') or {}
        title=_t(r.get('title')) or _t(r.get('description')) or _t(r.get('issue_id')) or knowledge_id
        root=_t(occ.get('root_cause')) or _t(r.get('occurrence_root_cause')) or _t(r.get('occurrence_original_reason'))
        escape_root=_t(esc.get('root_cause')) or _t(r.get('escape_root_cause')) or _t(r.get('escape_original_reason'))
        tags=_dedupe([
            r.get('business_type'),r.get('product'),r.get('platform'),r.get('module'),r.get('feature_l1'),r.get('feature_l2'),
            r.get('occurrence_l1'),r.get('occurrence_l2'),r.get('escape_l1'),r.get('escape_l2'),
            *[g.get('gap_category') for g in gaps]
        ])
        parts=[
            f"案例：{knowledge_id} / {_t(r.get('issue_id'))}",
            f"业务：{_t(r.get('business_type'))}",
            f"产品：{_t(r.get('product'))} / {_t(r.get('platform'))} / {_t(r.get('module'))}",
            f"问题：{title}",
            f"描述：{_t(r.get('description'))}",
            f"影响：{_t(r.get('impact'))}",
            f"发生根因：{root}",
            f"流出根因：{escape_root}",
            f"发生分类：{_t(r.get('occurrence_l1'))} / {_t(r.get('occurrence_l2'))}",
            f"流出分类：{_t(r.get('escape_l1'))} / {_t(r.get('escape_l2'))}",
            f"解决方案：{_t(r.get('original_solution'))}",
            f"技术措施：{_t(r.get('technical_action'))}",
            f"管理措施：{_t(r.get('management_action'))}",
            f"再发风险：{_t(rec.get('recurrence_risk_level'))}",
            f"能力缺口：{'、'.join(_dedupe(g.get('gap_category') for g in gaps))}",
            f"标签：{'、'.join(tags)}",
        ]
        text='\n'.join(p for p in parts if not p.endswith('：') and not p.endswith('： / '))
        content_hash=hashlib.sha256(text.encode('utf-8')).hexdigest()
        case_id=f"QK-{knowledge_id}"
        source_path=f"knowledge/quality_retrieval_source/{case_id}.json"
        retrieval={
            'document_id':f'DOC-{case_id}','case_id':case_id,'itr_id':_t(r.get('issue_id')),
            'source_case_path':source_path,
            'organization':{'ipmt':'','spdt':_t(r.get('business_group')),'responsible_department_level2':_t(r.get('department'))},
            'classification':{'cause_level1':_t(r.get('occurrence_l1')),'cause_level2':_t(r.get('occurrence_l2')),'source':'ORIGINAL' if _t(r.get('occurrence_l1')) else 'EMPTY'},
            'filters':{'assessment_year':_t(r.get('month'))[:4],'assessment_month':_t(r.get('month')),'product':_t(r.get('product')),'domain':_t(r.get('business_type')),'has_report':False,'classification_conflict':False,'knowledge_source':'QUALITY_ISSUE_SQLITE','knowledge_id':knowledge_id},
            'title':title,'text':text,'tags':tags,'quality_flags':[], 'content_hash':content_hash,
            'generated_at':datetime.now(timezone.utc).isoformat(),
        }
        # Minimal compatibility source artifact consumed by CandidateLoader/KnowledgeService.
        source={
            'metadata':{'case_id':case_id,'itr_id':_t(r.get('issue_id')),'quality_knowledge_id':knowledge_id,'source_type':'QUALITY_ISSUE_SQLITE'},
            'business_context':{'product':_t(r.get('product')),'domain':_t(r.get('business_type')),'spdt':_t(r.get('business_group'))},
            'problem':{'original_description':_t(r.get('description')),'standard_description':title,'impact':_t(r.get('impact'))},
            'analysis':{'root_cause':[{'value':root}] if root else [],'escape_root_cause':[{'value':escape_root}] if escape_root else []},
            'solution':{'corrective_actions':[{'value':_t(r.get('corrective_action'))}] if _t(r.get('corrective_action')) else [],'preventive_actions':[{'value':_t(r.get('improvement_action'))}] if _t(r.get('improvement_action')) else [],'reusable_actions':[{'value':_t(r.get('reusable_action'))}] if _t(r.get('reusable_action')) else []},
            'knowledge':{'normalized_problem':title,'retrieval_text':text,'keywords':tags,'quality_flags':[]},
            'quality_issue_extension':{'occurrence_analysis':occ,'escape_analysis':esc,'recurrence_analysis':rec,'capability_gaps':gaps},
        }
        return retrieval, source

    def publish(self, knowledge_service, knowledge_id: str, *, overwrite: bool=False):
        retrieval, source=self.build(knowledge_id)
        case_id=retrieval['case_id']
        rp=f'knowledge/retrieval_docs/{case_id}.json'
        sp=retrieval['source_case_path']
        existing=knowledge_service.repository.load(rp)
        if existing and not overwrite and existing.get('content_hash')==retrieval['content_hash']:
            return {'knowledge_id':knowledge_id,'case_id':case_id,'status':'SKIPPED','retrieval_doc_path':rp}
        knowledge_service.repository.save(sp,source)
        knowledge_service.repository.save(rp,retrieval)
        return {'knowledge_id':knowledge_id,'case_id':case_id,'status':'PUBLISHED','retrieval_doc_path':rp,'source_case_path':sp}
