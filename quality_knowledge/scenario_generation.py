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
输出严格 JSON：{"items":[{"name":"","lifecycle_code":"","activity_code":"","experience_requirement":"","concern_points":"","quality_attribute":"","quality_subcharacteristic":"","applicable_boundary":"","validation_direction":"","measurement_suggestion":"","evidence_issue_ids":[],"evidence_summary":"","confidence":0.0,"confirmation_questions":[]}]}
要求：只能使用给定词典编码；每一个输入问题必须且只能出现在一个场景的 evidence_issue_ids 中，不得遗漏；每项必须有来源问题；掉电、断电、保持变量丢失、上电恢复异常等问题必须优先选择 POWER_LOSS_RETENTION_RECOVERY；场景链路由系统根据 activity_code 从场景配置表读取，不需要输出；彻底解决单中的 occurrence_phase（原始问题发生阶段）只作为推断标准生命周期和业务活动的参考证据，不得直接照搬为最终分类，但“终端正常使用”且没有配置操作证据时不得归入 ENGINEERING_CONFIGURATION；客户、行业、客户分级和客户状态只作为场景适用范围与证据，不得虚构；quality_attribute 填质量特性，quality_subcharacteristic 填更具体的质量子特性；measurement_suggestion 必须包含建议指标、度量/计算方法和观测条件，数据不足时只给度量建议，不虚构阈值；不确定内容写入 confirmation_questions，禁止猜测；相同阶段、业务活动、质量子特性和失效表现可合并；每个文本字段不超过160个汉字；只输出JSON，不要解释、Markdown或代码围栏。"""


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
                'items':[{'knowledge_id':x['knowledge_id'],'business_issue_id':x.get('business_issue_id'),'title':x.get('title') or x.get('description') or '未提供问题描述','month':x.get('month'),'severity':x.get('severity')} for x in analysed]}

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
            'customer_level':('问题信息_客户分级','客户分级'),'customer_status':('问题信息_当前客户状态','问题信息_当前问题状态','问题信息_问题状态','当前客户状态','当前问题状态'),
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
        activities={x['activity_code']:(x['activity_code'],x['lifecycle_code'],x.get('chain_text') or '') for x in taxonomy['activities'] if x['enabled']};activities.update({x['label_zh']:(x['activity_code'],x['lifecycle_code'],x.get('chain_text') or '') for x in taxonomy['activities'] if x['enabled']})
        def evidence_text(evidence_ids):
            related=[]
            for record in payload:
                record_ids={str(record.get('knowledge_id') or ''),str(record.get('business_issue_id') or '')}|{str(x) for x in record.get('evidence_issue_ids',[]) if x}
                if record_ids.intersection(str(x) for x in evidence_ids):related.append(record)
            return json.dumps(related,ensure_ascii=False,default=str)
        items=[]
        for raw in parsed['items'][:5]:
            if not isinstance(raw,dict): continue
            evidence=list(dict.fromkeys(allowed[str(x)] for x in raw.get('evidence_issue_ids',[]) if str(x) in allowed))[:30]
            lifecycle=life.get(raw.get('lifecycle_code'));activity=activities.get(raw.get('activity_code'))
            if not evidence or not activity: continue
            if not lifecycle:lifecycle=activity[1]
            context=evidence_text(evidence);terminal_override=False
            if re.search(r'掉电|断电|重新上电|上电恢复|保持变量|数据保持|保持数据|计数清零',context):
                activity=activities.get('POWER_LOSS_RETENTION_RECOVERY') or activity;lifecycle=activity[1]
            elif lifecycle=='ENGINEERING_CONFIGURATION' and re.search(r'终端正常使用|客户正常使用|正常运行阶段',context) and not re.search(r'用户.{0,6}(配置|组态|编程|编译)|执行.{0,4}(配置|组态|编程|编译)',context):
                activity=activities.get('STATE_DATA_PROCESSING') or activity;lifecycle=activity[1];terminal_override=True
            if activity[1]!=lifecycle:continue
            item={key:raw.get(key,'') for key in ('name','lifecycle_code','activity_code','experience_requirement','concern_points','quality_attribute','quality_subcharacteristic','applicable_boundary','validation_direction','measurement_suggestion','evidence_summary')}
            item['lifecycle_code']=lifecycle;item['activity_code']=activity[0]
            item['scenario_chain']=activity[2]
            item.update({'evidence_issue_ids':evidence,'confidence':max(0,min(1,float(raw.get('confidence') or 0))),'confirmation_questions':[str(x) for x in raw.get('confirmation_questions',[]) if str(x).strip()][:5]})
            if terminal_override:
                item['confirmation_questions']=(item['confirmation_questions']+['原始阶段为终端正常使用，请确认实际业务活动是否属于设备状态与数据处理'])[:5]
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
        self.scenarios.initialize_issue_classifications(generation_id,records)
        self.scenarios.update_generation(generation_id,status='RUNNING',progress_text=f'正在分批分析 {len(records)} 个问题')
        mapped=[];model=str(cfg.get('model') or getattr(client,'model',''))
        for start in range(0,len(records),batch_size):
            chunk=records[start:start+batch_size];allowed={str(x['knowledge_id']):x['knowledge_id'] for x in chunk};allowed.update({str(x.get('business_issue_id')):x['knowledge_id'] for x in chunk if x.get('business_issue_id')});items,model=self._complete(client,chunk,allowed,compact_taxonomy);mapped.extend(items)
            covered={kid for item in items for kid in item.get('evidence_issue_ids',[])}
            missing=[row for row in chunk if row['knowledge_id'] not in covered]
            for row in missing:
                one_allowed={str(row['knowledge_id']):row['knowledge_id']}
                if row.get('business_issue_id'):one_allowed[str(row['business_issue_id'])]=row['knowledge_id']
                try:
                    retry,model=self._complete(client,[row],one_allowed,compact_taxonomy)
                except Exception as error:
                    retry=[];self.scenarios.mark_issue_classification(generation_id,row['knowledge_id'],'FAILED',error_message=str(error))
                if retry:mapped.extend(retry);covered.update(k for item in retry for k in item.get('evidence_issue_ids',[]))
            for row in chunk:
                if row['knowledge_id'] in covered:self.scenarios.mark_issue_classification(generation_id,row['knowledge_id'],'CLASSIFIED')
                elif not any(x['knowledge_id']==row['knowledge_id'] and x['status']=='FAILED' for x in self.scenarios.issue_classifications(generation_id)):
                    self.scenarios.mark_issue_classification(generation_id,row['knowledge_id'],'REVIEW_REQUIRED',error_message='AI未返回该问题的有效场景，请人工确认')
            coverage=self.scenarios.refresh_generation_coverage(generation_id)
            self.scenarios.update_generation(generation_id,progress_text=f"已处理 {coverage['processed_count']}/{len(records)}；已识别 {coverage['classified_count']}，待确认 {coverage['review_required_count']}，失败 {coverage['failed_count']}")
        if not mapped:raise ValueError('AI未生成有效候选：请检查模型输出的场景名称、生命周期、业务活动和来源问题')
        # 跨批次只合并语义完全一致的基础场景，行业、客户和产品差异保留在来源范围中。
        merged={}
        for item in mapped:
            key=(item.get('activity_code',''),item.get('quality_subcharacteristic',''),re.sub(r'\s+','',item.get('name','')).lower())
            if key not in merged:merged[key]=item;continue
            target=merged[key];target['evidence_issue_ids']=list(dict.fromkeys(target.get('evidence_issue_ids',[])+item.get('evidence_issue_ids',[])))
            target['confirmation_questions']=list(dict.fromkeys(target.get('confirmation_questions',[])+item.get('confirmation_questions',[])))[:5]
            target['confidence']=min(float(target.get('confidence') or 0),float(item.get('confidence') or 0))
        mapped=list(merged.values())
        created=[]
        for index,item in enumerate(mapped,1):
            code=f"AI-{hashlib.sha1((generation_id+str(index)).encode()).hexdigest()[:10].upper()}"
            source_records=[x for x in records if x['knowledge_id'] in item.get('evidence_issue_ids',[])]
            scope_fields={'IPMT':'ipmt','SPDT':'spdt','PRODUCT_MODEL':'product_model','INDUSTRY':'customer_industry','CUSTOMER_NAME':'customer_name','CUSTOMER_LEVEL':'customer_level','CUSTOMER_STATUS':'customer_status','OCCURRENCE_PHASE':'occurrence_phase'}
            scopes={kind:sorted({x['itr_cs_context'].get(field,'') for x in source_records}-{''}) for kind,field in scope_fields.items()}
            scenario_id=self.scenarios.save_generated_candidate(code,item,scopes,generation_id,product,start_month,end_month,model)
            for knowledge_id in item.get('evidence_issue_ids',[]):
                self.scenarios.mark_issue_classification(generation_id,knowledge_id,'CLASSIFIED',scenario_id=scenario_id,activity_code=item.get('activity_code',''),lifecycle_code=item.get('lifecycle_code',''))
            created.append(scenario_id)
        coverage=self.scenarios.refresh_generation_coverage(generation_id)
        final_status='COMPLETED' if coverage['unprocessed_count']==0 and coverage['failed_count']==0 and coverage['review_required_count']==0 else 'PARTIAL'
        text=f"覆盖 {coverage['classified_count']}/{len(records)}；待确认 {coverage['review_required_count']}，失败 {coverage['failed_count']}"
        self.scenarios.update_generation(generation_id,status=final_status,progress_text=text,candidate_count=len(created),model_name=model,error_message='' if final_status=='COMPLETED' else '部分问题未形成有效场景，请处理待确认或失败项')
        return {'generation_id':generation_id,'source_issue_count':len(records),'candidate_count':len(created),'scenario_ids':created,'model':model,**coverage,'status':final_status}

    def run_job(self,generation_id,product,start_month,end_month,selected_ids,created_by='WEB_USER'):
        try:return self.generate(product,start_month,end_month,created_by,selected_ids,generation_id)
        except Exception as error:
            self.scenarios.update_generation(generation_id,status='FAILED',progress_text='生成失败',error_message=str(error));return None
