from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path

from builder.ai_client import OpenAICompatibleClient
from builder.json_response import parse_json_object
from quality_knowledge.model_config import load_quality_issue_ai_config


PROMPT = """/no_think
你是资深产品质量与测试专家。请根据历史客户问题提炼可复用的产品质量场景，而不是复述问题。不要输出思考过程。
输出严格 JSON：{"items":[{"name":"","lifecycle_code":"","activity_code":"","experience_requirement":"","concern_points":"","quality_attribute":"","applicable_boundary":"","validation_direction":"","evidence_issue_ids":[],"evidence_summary":"","confidence":0.0,"confirmation_questions":[]}]}
要求：只能使用给定词典编码；每项必须有来源问题；不确定内容写入 confirmation_questions，禁止猜测；相同阶段、业务活动、客户体验和失效表现应合并；最多3项；每个文本字段不超过120个汉字；只输出JSON，不要解释、Markdown或代码围栏。"""


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
        return {'issue_count': len(scoped), 'analysed_count': len(analysed), 'ready': bool(analysed), 'coverage_rate': round(len(analysed)*100/len(scoped),1) if scoped else 0,
                'items':[{'knowledge_id':x['knowledge_id'],'business_issue_id':x.get('business_issue_id'),'title':x.get('title'),'month':x.get('month'),'severity':x.get('severity')} for x in analysed]}

    @staticmethod
    def _value(data, *names):
        for name in names:
            value = data.get(name) if isinstance(data, dict) else None
            if isinstance(value, dict): value = value.get('value') or value.get('label_zh')
            if value not in (None, '', [], {}): return value
        return ''

    def _cs_context(self,knowledge_id):
        aliases={
            'ipmt':('问题信息_IPMT','IPMT'),'spdt':('问题信息_SPDT','SPDT'),'product_model':('问题信息_产品型号','产品型号'),
            'customer_industry':('问题信息_客户行业','客户行业'),'customer_name':('问题信息_客户名称','客户名称'),
            'customer_level':('问题信息_客户分级','客户分级'),'customer_status':('问题信息_当前客户状态','当前客户状态'),
            'occurrence_phase':('问题信息_问题发生阶段','问题发生阶段'),'root_cause':('问题信息_问题原因定位','问题原因定位'),
            'trc_correction':('技术根因分析与纠正_TRC纠正信息','TRC纠正信息')}
        try:
            with self.scenarios.connect() as c:
                row=c.execute("SELECT m.raw_json FROM issue_material_link l JOIN source_material m ON m.material_id=l.material_id WHERE l.knowledge_id=? AND m.material_type='ITR_CS' ORDER BY m.version_no DESC LIMIT 1",(knowledge_id,)).fetchone()
        except Exception:return {}
        if not row:return {}
        raw=json.loads(row[0] or '{}');result={}
        for key,names in aliases.items():
            result[key]=next((str(raw.get(name) or '').strip() for name in names if str(raw.get(name) or '').strip()),'')
        return {key:value for key,value in result.items() if value}

    def _records(self, product, start_month, end_month, selected_ids=None):
        lo, hi = self._month(start_month), self._month(end_month)
        rows = self.issues.query_issues({'business_type': product} if product else {}, 100000)
        records=[]
        selected=set(selected_ids or [])
        for row in rows:
            if not lo <= self._month(row.get('month')) <= hi: continue
            if selected and row['knowledge_id'] not in selected:continue
            analyses={stage:self.issues.get_latest_analysis(row['knowledge_id'],stage) for stage in ('occurrence','escape','recurrence','capability_gap')}
            if not any(analyses.values()): continue
            results={stage:(analyses[stage] or {}).get('result') or {} for stage in analyses}
            occurrence=results['occurrence'];escape=results['escape'];recurrence=results['recurrence'];raw_gaps=results['capability_gap'].get('capability_gaps') or []
            records.append({
                'knowledge_id':row['knowledge_id'],'business_issue_id':row.get('business_issue_id'),'title':row.get('title'),
                'description':str(row.get('description') or '')[:300],'month':row.get('month'),'severity':row.get('severity'),
                'occurrence':{'root_cause':str(self._value(occurrence,'root_cause_summary','root_cause'))[:240],'failure_mechanism':str(self._value(occurrence,'failure_mechanism'))[:240],'category':str(self._value(occurrence,'occurrence_category'))[:80]},
                'escape':{'reason':str(self._value(escape,'escape_cause_summary','escape_reason'))[:240],'verification_gap':str(self._value(escape,'verification_gap'))[:240],'missing_control':str(self._value(escape,'missing_control'))[:160]},
                'recurrence':{'risk':str(self._value(recurrence,'recurrence_risk_level'))[:40],'customer_impact':str(self._value(recurrence,'customer_impact'))[:200]},
                'capability_gaps':[{'dimension':str(gap.get('dimension') or '')[:40],'category':str(gap.get('category') or '')[:80],'description':str(gap.get('description') or gap.get('gap_description') or '')[:200]} for gap in raw_gaps[:4] if isinstance(gap,dict)],
                'itr_cs_context':self._cs_context(row['knowledge_id']),
            })
        return records

    def _complete(self, client, payload, allowed, taxonomy):
        response=client.complete([{'role':'system','content':PROMPT},{'role':'user','content':json.dumps({'taxonomy':taxonomy,'records':payload},ensure_ascii=False,default=str)}])
        parsed,_=parse_json_object(response.content,allow_repair=True)
        if not isinstance(parsed,dict) or not isinstance(parsed.get('items'),list): raise ValueError('SCENARIO_AI_SCHEMA_INVALID')
        life={x['lifecycle_code']:x['lifecycle_code'] for x in taxonomy['lifecycles'] if x['enabled']};life.update({x['label_zh']:x['lifecycle_code'] for x in taxonomy['lifecycles'] if x['enabled']})
        activities={x['activity_code']:(x['activity_code'],x['lifecycle_code']) for x in taxonomy['activities'] if x['enabled']};activities.update({x['label_zh']:(x['activity_code'],x['lifecycle_code']) for x in taxonomy['activities'] if x['enabled']})
        items=[]
        for raw in parsed['items'][:5]:
            if not isinstance(raw,dict): continue
            evidence=list(dict.fromkeys(allowed[str(x)] for x in raw.get('evidence_issue_ids',[]) if str(x) in allowed))[:30]
            lifecycle=life.get(raw.get('lifecycle_code'));activity=activities.get(raw.get('activity_code'))
            if not evidence or not activity: continue
            if not lifecycle:lifecycle=activity[1]
            if activity[1]!=lifecycle:continue
            item={key:raw.get(key,'') for key in ('name','lifecycle_code','activity_code','experience_requirement','concern_points','quality_attribute','applicable_boundary','validation_direction','evidence_summary')}
            item['lifecycle_code']=lifecycle;item['activity_code']=activity[0]
            item.update({'evidence_issue_ids':evidence,'confidence':max(0,min(1,float(raw.get('confidence') or 0))),'confirmation_questions':[str(x) for x in raw.get('confirmation_questions',[]) if str(x).strip()][:5]})
            if item['name']: items.append(item)
        return items,response.model

    def generate(self, product, start_month, end_month, created_by='WEB_USER',selected_ids=None,generation_id=''):
        records=self._records(product,start_month,end_month,selected_ids)
        if not records: raise ValueError('SCENARIO_SOURCE_ANALYSIS_REQUIRED')
        taxonomy=self.scenarios.taxonomy_active()
        compact_taxonomy={'lifecycles':taxonomy['lifecycles'],'activities':taxonomy['activities']}
        cfg={}
        if self.ai_client is None:
            cfg,_=load_quality_issue_ai_config(self.root,agent_id='DEFAULT')
        client=self.ai_client or OpenAICompatibleClient({**cfg,'max_tokens':max(8192,int(cfg.get('scenario_generation_max_tokens') or 8192)),'temperature':0})
        batch_size=max(5,min(30,int(cfg.get('scenario_generation_batch_size') or 15)))
        generation_id=generation_id or f"QSG-{uuid.uuid4().hex}"
        if not self.scenarios.generation(generation_id):self.scenarios.create_generation(generation_id,product,start_month,end_month,len(records),created_by)
        self.scenarios.update_generation(generation_id,status='RUNNING',progress_text=f'正在分批分析 {len(records)} 个问题')
        mapped=[];model=str(cfg.get('model') or getattr(client,'model',''))
        for start in range(0,len(records),batch_size):
            chunk=records[start:start+batch_size];allowed={str(x['knowledge_id']):x['knowledge_id'] for x in chunk};allowed.update({str(x.get('business_issue_id')):x['knowledge_id'] for x in chunk if x.get('business_issue_id')});items,model=self._complete(client,chunk,allowed,compact_taxonomy);mapped.extend(items)
            self.scenarios.update_generation(generation_id,progress_text=f'已完成 {min(start+len(chunk),len(records))}/{len(records)} 个问题')
        if len(records)>batch_size and mapped:
            reduced_input=[{**x,'source_count':len(x['evidence_issue_ids'])} for x in mapped]
            reduced_allowed={str(i):i for x in mapped for i in x['evidence_issue_ids']};reduced,model=self._complete(client,reduced_input,reduced_allowed,compact_taxonomy)
            if reduced:mapped=reduced
        if not mapped:raise ValueError('AI未生成有效候选：请检查模型输出的场景名称、生命周期、业务活动和来源问题')
        created=[]
        for index,item in enumerate(mapped,1):
            code=f"AI-{hashlib.sha1((generation_id+str(index)).encode()).hexdigest()[:10].upper()}"
            source_records=[x for x in records if x['knowledge_id'] in item.get('evidence_issue_ids',[])]
            scopes={'IPMT':sorted({x['itr_cs_context'].get('ipmt','') for x in source_records}-{''}),'SPDT':sorted({x['itr_cs_context'].get('spdt','') for x in source_records}-{''}),'PRODUCT_MODEL':sorted({x['itr_cs_context'].get('product_model','') for x in source_records}-{''})}
            scenario_id=self.scenarios.save_generated_candidate(code,item,scopes,generation_id,product,start_month,end_month,model)
            created.append(scenario_id)
        self.scenarios.update_generation(generation_id,status='COMPLETED',progress_text='生成完成，等待人工审核',candidate_count=len(created),model_name=model,error_message='')
        return {'generation_id':generation_id,'source_issue_count':len(records),'candidate_count':len(created),'scenario_ids':created,'model':model}

    def run_job(self,generation_id,product,start_month,end_month,selected_ids,created_by='WEB_USER'):
        try:return self.generate(product,start_month,end_month,created_by,selected_ids,generation_id)
        except Exception as error:
            self.scenarios.update_generation(generation_id,status='FAILED',progress_text='生成失败',error_message=str(error));return None
