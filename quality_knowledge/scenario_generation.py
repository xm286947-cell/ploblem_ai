from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path

from builder.ai_client import OpenAICompatibleClient
from builder.json_response import parse_json_object
from quality_knowledge.model_config import load_quality_issue_ai_config


PROMPT = """你是资深产品质量与测试专家。请根据历史客户问题提炼可复用的产品质量场景，而不是复述问题。
输出严格 JSON：{"items":[{"name":"","lifecycle_code":"","activity_code":"","experience_requirement":"","concern_points":"","quality_attribute":"","applicable_boundary":"","validation_direction":"","evidence_issue_ids":[],"evidence_summary":"","confidence":0.0,"confirmation_questions":[]}]}
要求：只能使用给定词典编码；每项必须有来源问题；不确定内容写入 confirmation_questions，禁止猜测；相同阶段、业务活动、客户体验和失效表现应合并；最多5项；中文输出。"""


class ScenarioGenerationService:
    def __init__(self, issue_service, scenario_repository, root: str | Path, ai_client=None):
        self.issues = issue_service
        self.scenarios = scenario_repository
        self.root = Path(root)
        self.ai_client = ai_client

    @staticmethod
    def _month(value):
        match = re.search(r"\d+", str(value or ""))
        return int(match.group()) if match else -1

    def precheck(self, product: str, start_month: str, end_month: str):
        lo, hi = self._month(start_month), self._month(end_month)
        rows = self.issues.query_issues({'business_type': product} if product else {}, 100000)
        scoped = [row for row in rows if lo <= self._month(row.get('month')) <= hi]
        analysed = [row for row in scoped if any(self.issues.get_latest_analysis(row['knowledge_id'], stage) for stage in ('occurrence','escape','recurrence','capability_gap'))]
        return {'issue_count': len(scoped), 'analysed_count': len(analysed), 'ready': bool(analysed), 'coverage_rate': round(len(analysed)*100/len(scoped),1) if scoped else 0}

    @staticmethod
    def _value(data, *names):
        for name in names:
            value = data.get(name) if isinstance(data, dict) else None
            if isinstance(value, dict): value = value.get('value') or value.get('label_zh')
            if value not in (None, '', [], {}): return value
        return ''

    def _records(self, product, start_month, end_month):
        lo, hi = self._month(start_month), self._month(end_month)
        rows = self.issues.query_issues({'business_type': product} if product else {}, 100000)
        records=[]
        for row in rows:
            if not lo <= self._month(row.get('month')) <= hi: continue
            analyses={stage:self.issues.get_latest_analysis(row['knowledge_id'],stage) for stage in ('occurrence','escape','recurrence','capability_gap')}
            if not any(analyses.values()): continue
            results={stage:(analyses[stage] or {}).get('result') or {} for stage in analyses}
            records.append({
                'knowledge_id':row['knowledge_id'],'business_issue_id':row.get('business_issue_id'),'title':row.get('title'),
                'description':str(row.get('description') or '')[:500],'month':row.get('month'),'severity':row.get('severity'),
                'occurrence':results['occurrence'],'escape':results['escape'],'recurrence':results['recurrence'],
                'capability_gaps':(results['capability_gap'].get('capability_gaps') or [])[:6],
            })
        return records

    def _complete(self, client, payload, allowed, taxonomy):
        response=client.complete([{'role':'system','content':PROMPT},{'role':'user','content':json.dumps({'taxonomy':taxonomy,'records':payload},ensure_ascii=False,default=str)}])
        parsed,_=parse_json_object(response.content,allow_repair=True)
        if not isinstance(parsed,dict) or not isinstance(parsed.get('items'),list): raise ValueError('SCENARIO_AI_SCHEMA_INVALID')
        life={x['lifecycle_code'] for x in taxonomy['lifecycles'] if x['enabled']};activities={x['activity_code']:x['lifecycle_code'] for x in taxonomy['activities'] if x['enabled']}
        items=[]
        for raw in parsed['items'][:5]:
            if not isinstance(raw,dict): continue
            evidence=[str(x) for x in raw.get('evidence_issue_ids',[]) if str(x) in allowed][:30]
            if not evidence or raw.get('lifecycle_code') not in life or activities.get(raw.get('activity_code'))!=raw.get('lifecycle_code'): continue
            item={key:raw.get(key,'') for key in ('name','lifecycle_code','activity_code','experience_requirement','concern_points','quality_attribute','applicable_boundary','validation_direction','evidence_summary')}
            item.update({'evidence_issue_ids':evidence,'confidence':max(0,min(1,float(raw.get('confidence') or 0))),'confirmation_questions':[str(x) for x in raw.get('confirmation_questions',[]) if str(x).strip()][:5]})
            if item['name']: items.append(item)
        return items,response.model

    def generate(self, product, start_month, end_month, created_by='WEB_USER'):
        records=self._records(product,start_month,end_month)
        if not records: raise ValueError('SCENARIO_SOURCE_ANALYSIS_REQUIRED')
        taxonomy=self.scenarios.taxonomy_active()
        compact_taxonomy={'lifecycles':taxonomy['lifecycles'],'activities':taxonomy['activities']}
        cfg={}
        if self.ai_client is None:
            cfg,_=load_quality_issue_ai_config(self.root,agent_id='DEFAULT')
        client=self.ai_client or OpenAICompatibleClient({**cfg,'max_tokens':max(4096,int(cfg.get('scenario_generation_max_tokens') or 4096)),'temperature':0})
        batch_size=max(5,min(30,int(cfg.get('scenario_generation_batch_size') or 15)))
        mapped=[];model=str(cfg.get('model') or getattr(client,'model',''))
        for start in range(0,len(records),batch_size):
            chunk=records[start:start+batch_size];items,model=self._complete(client,chunk,{x['knowledge_id'] for x in chunk},compact_taxonomy);mapped.extend(items)
        if len(records)>batch_size and mapped:
            reduced_input=[{**x,'source_count':len(x['evidence_issue_ids'])} for x in mapped]
            reduced,model=self._complete(client,reduced_input,{i for x in mapped for i in x['evidence_issue_ids']},compact_taxonomy)
            if reduced:mapped=reduced
        generation_id=f"QSG-{uuid.uuid4().hex}"
        created=[]
        for index,item in enumerate(mapped,1):
            code=f"AI-{hashlib.sha1((generation_id+str(index)).encode()).hexdigest()[:10].upper()}"
            scenario_id=self.scenarios.save_generated_candidate(code,item,{'PRODUCT_MODEL':[]},generation_id,product,start_month,end_month,model)
            created.append(scenario_id)
        self.scenarios.save_generation(generation_id,product,start_month,end_month,len(records),len(created),model,created_by)
        return {'generation_id':generation_id,'source_issue_count':len(records),'candidate_count':len(created),'scenario_ids':created,'model':model}
