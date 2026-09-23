from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path

import yaml

from builder.ai_client import OpenAICompatibleClient
from builder.execution_engine import ExecutionEngine, ExecutionPlan
from builder.json_response import parse_json_object
from quality_knowledge.model_config import choose_quality_issue_agent, load_quality_issue_ai_config, resolve_model_config_path
from quality_knowledge.reverse_quality_scenario_adapter import adapt_reverse_quality_result


STANDARDIZATION_PROMPT = """/no_think
你是资深质量工程专家。对一个既有质量场景补齐标准化分类，不改变场景名称、业务活动、发布状态和来源证据。只输出严格JSON：
{"customer_perception":"","primary_typical_problem_code":"","secondary_typical_problem_codes":[],"primary_quality_concern_code":"","secondary_quality_concern_codes":[],"primary_customer_experience_statement":"","secondary_customer_experience_statements":[],"operating_environment":"未知","operating_condition":"未知","duration_frequency":"未知","disturbances":"未知","extreme_conditions":"未知","environment_condition_codes":[],"primary_experience_code":"","secondary_experience_codes":[],"quality_in_use_codes":[],"primary_quality_characteristic_code":"","secondary_quality_characteristic_codes":[],"quality_subcharacteristic_codes":[]}
所有code只能从提供的词典选择；质量子特性必须属于已选择的主要或次要产品质量特性。典型问题写客户看到的短标签，质量关注点写需守住的质量对象；客户体验表述必须使用“我希望……不要……否则……”一类客户语言。环境工况按客观证据拆分，无证据写“未知”。禁止输出解释、Markdown或新增字段。"""

PROMPT = """/no_think
你是资深产品质量与测试专家。请根据历史客户问题提炼可复用的产品质量场景，而不是复述问题。不要输出思考过程。
输出严格 JSON：{"items":[{"name":"","lifecycle_code":"","activity_code":"","customer_perception":"","primary_typical_problem_code":"","secondary_typical_problem_codes":[],"primary_quality_concern_code":"","secondary_quality_concern_codes":[],"primary_customer_experience_statement":"","secondary_customer_experience_statements":[],"operating_environment":"","operating_condition":"","duration_frequency":"","disturbances":"","extreme_conditions":"","environment_condition_codes":[],"proposed_semantic_terms":[],"primary_experience_code":"","secondary_experience_codes":[],"quality_in_use_codes":[],"primary_quality_characteristic_code":"","secondary_quality_characteristic_codes":[],"quality_subcharacteristic_codes":[],"experience_requirement":"","concern_points":"","failure_mode":"","failure_mechanism":"","trigger_conditions":"","preconditions":"","participating_systems":"","system_scale":"","user_type":"","affected_object":"","business_impact":"","recovery_method":"","applicable_boundary":"","validation_direction":"","measurement_suggestion":"","evidence_issue_ids":[],"evidence_summary":"","confidence":0.0,"confirmation_questions":[]}]}
要求：当前输入始终只有一个问题，只输出一个 items 元素；该问题必须且只能出现在该元素的 evidence_issue_ids 中，不得遗漏、不得与其他问题合并；每项必须有来源问题；客户质量体验、使用质量要素、产品质量特性和质量子特性只能使用 quality_models 中给定的 code，禁止自由造词；质量子特性必须属于已选择的主要或次要产品质量特性；customer_perception 填客户直接感知的负向表现；掉电保持活动仅在产品词典中存在且业务链路匹配时选择，不得仅因关键词覆盖其他阶段；场景链路由系统根据 activity_code 从场景配置表读取，不需要输出；彻底解决单中的 occurrence_phase（原始问题发生阶段）只作为推断标准生命周期和业务活动的参考证据，不得直接照搬为最终分类，但“终端正常使用”且没有配置操作证据时不得归入 ENGINEERING_CONFIGURATION；客户、行业、客户分级和客户状态只作为场景适用范围与证据，不得虚构；measurement_suggestion 必须包含建议指标、度量/计算方法和观测条件，数据不足时只给度量建议，不虚构阈值；不确定内容写入 confirmation_questions，禁止猜测；每个文本字段不超过160个汉字；只输出JSON，不要解释、Markdown或代码围栏。"""

PROMPT += """
语义收敛规则：优先从 semantic_dictionary 选择正式术语。primary_customer_experience_statement 必须写成客户语言，如“我希望……时……，不要……，否则会……”，不得只填“可靠、易恢复”等抽象词；secondary_customer_experience_statements 同理。
典型问题回答“客户看到了什么”，不得写内存泄漏、线程死锁等根因；质量关注点回答“研发和测试必须守住什么质量对象”，不得复述整段问题。
必须分别填写 operating_environment（软硬件/网络/部署环境）、operating_condition（负载与运行状态）、duration_frequency（时长/频次）、disturbances（异常扰动）、extreme_conditions（极限边界）；无证据写“未知”，不得猜测。
若没有合适词典项，proposed_semantic_terms 可提出候选，结构为 {"term_type":"TYPICAL_PROBLEM|QUALITY_CONCERN|ENVIRONMENT_CONDITION","term_code":"大写英文编码","label_zh":"短标签","definition":"定义","inclusion_criteria":"纳入条件","exclusion_criteria":"排除条件","nearest_term_code":"最接近正式术语","difference_note":"不能复用的原因"}；候选会进入人工审核，禁止仅因措辞不同新增。
"""

PROMPT += """
阶段判定必须比较三种解释，并在 lifecycle_assessment 中为 RUNTIME_EXECUTION、SYSTEM_INTEGRATION、LONG_TERM_OPERATION 分别填写支持证据、反证或证据不足；在 lifecycle_reason 中说明主阶段和排除其他阶段的理由。
运行执行：单系统控制或处理任务出错，不以跨系统交互或时间累积为必要条件。
系统联动：故障依赖多个系统之间指令、状态、时序、数据一致性或协同过程；仅出现通信词语不足以判定。
长稳运行：持续时长、反复循环或资源累积是故障触发的必要条件；仅出现稳定性或内存泄漏词语不足以判定。
出现多种条件时，以客户正在完成的主要业务活动选主阶段，并在 operating_conditions 保留其他工况；证据不足必须在 confirmation_questions 标明，不可默认运行执行。
新增JSON字段：lifecycle_assessment（对象，键为上述三个阶段代码、值为证据说明）、lifecycle_reason（字符串）、operating_conditions（字符串）。
必须匹配当前产品启用词典中的生命周期和业务活动，活动必须属于选择的阶段。原始发生阶段仅供参考。若词典不支持所需活动，提出待确认，不要硬匹配。
存在 leakage_analysis 时优先使用其有效结论，缺失字段由 itr_cs_context 中根因、TRC纠正信息及解决方案补充。field_sources 是系统提供的来源事实，不得改写。缺少漏测流出原因不能虚构测试漏测结论。
硬件器件、机械和软件失效字段只能作为当前问题的证据；不要把器件更换后恢复等同于已经证明器件根因，也不要把一个设备的环境工况推广到同客户其他产品。
"""


class ScenarioGenerationService:
    def __init__(self, issue_service, scenario_repository, root: str | Path, ai_client=None):
        self.issues = issue_service
        self.scenarios = scenario_repository
        self.root = Path(root)
        self.ai_client = ai_client
        with self.scenarios.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS scenario_generation_source(generation_id TEXT PRIMARY KEY,records_json TEXT NOT NULL)')

    def save_source_snapshot(self,generation_id,records):
        with self.scenarios.connect() as c:
            c.execute('INSERT INTO scenario_generation_source VALUES(?,?)',(generation_id,json.dumps(records,ensure_ascii=False,default=str)))

    def source_snapshot(self,generation_id):
        with self.scenarios.connect() as c:
            row=c.execute('SELECT records_json FROM scenario_generation_source WHERE generation_id=?',(generation_id,)).fetchone()
        return json.loads(row[0]) if row else []

    def candidate_from_reverse_quality(self, result):
        payload=result.get('result') if isinstance(result,dict) and isinstance(result.get('result'),dict) else result
        if not isinstance(payload,dict):
            raise ValueError('REVERSE_QUALITY_RESULT_INVALID')
        identity=payload.get('identity') or {}
        product=str(identity.get('product_code') or '').strip()
        taxonomy_version_id=str(identity.get('taxonomy_version_id') or '').strip()
        if not product:
            raise ValueError('REVERSE_QUALITY_PRODUCT_REQUIRED')
        taxonomy=(self.scenarios.taxonomy(taxonomy_version_id,product)
                  if taxonomy_version_id else self.scenarios.taxonomy_active(product))
        if not taxonomy:
            raise ValueError('REVERSE_QUALITY_TAXONOMY_NOT_FOUND')
        return adapt_reverse_quality_result(result,taxonomy).to_dict()

    @staticmethod
    def analysis_identity(row,taxonomy_version_id,product):
        evidence={'canonical_itr':row.get('canonical_itr') or row.get('business_issue_id') or row.get('knowledge_id'),
                  'source_hashes':row.get('source_hashes') or [],'description':row.get('description') or '',
                  'leakage_analysis':row.get('leakage_analysis') or {},'occurrence':row.get('occurrence') or {},
                  'escape':row.get('escape') or {},'itr_cs_context':row.get('itr_cs_context') or {},
                  'supplemental_context':row.get('supplemental_context') or {}}
        evidence_hash=hashlib.sha256(json.dumps(evidence,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()
        identity={'task_type':'QUALITY_SCENARIO_V1','product':product,'taxonomy_version_id':taxonomy_version_id,
                  'prompt_version':hashlib.sha256(PROMPT.encode()).hexdigest(),'evidence_hash':evidence_hash}
        return hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest(),evidence_hash

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
        if selected_ids and all(str(x).startswith('MAT-') for x in selected_ids):
            from quality_knowledge.scenario_sources import operation_records,material_scene_records
            with self.scenarios.connect() as c:
                marks=','.join('?' for _ in selected_ids)
                kinds={r[0] for r in c.execute(f'SELECT material_type FROM source_material WHERE material_id IN ({marks})',tuple(selected_ids))}
            if kinds and kinds<={'ITR_CS','ITR_SOURCE'}:
                return material_scene_records(self,{'product_code':product},selected_ids)
            return operation_records(self,{'product_code':product},selected_ids)
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
                'description':str(row.get('description') or ''),'month':row.get('month'),'severity':row.get('severity'),
                'occurrence':{'root_cause':str(self._value(occurrence,'root_cause_summary','root_cause')),'failure_mechanism':str(self._value(occurrence,'failure_mechanism')),'category':str(self._value(occurrence,'occurrence_category'))[:80]},
                'escape':{'reason':str(self._value(escape,'escape_cause_summary','escape_reason')),'verification_gap':str(self._value(escape,'verification_gap')),'missing_control':str(self._value(escape,'missing_control'))[:160]},
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
            context=evidence_text(evidence)
            if activity[1]!=lifecycle:continue
            item={key:raw.get(key,'') for key in ('name','lifecycle_code','activity_code','customer_perception','primary_typical_problem_code','primary_quality_concern_code','primary_customer_experience_statement','operating_environment','operating_condition','duration_frequency','disturbances','extreme_conditions','primary_experience_code','primary_quality_characteristic_code','experience_requirement','concern_points','quality_attribute','quality_subcharacteristic','failure_mode','failure_mechanism','trigger_conditions','preconditions','participating_systems','system_scale','user_type','affected_object','business_impact','recovery_method','applicable_boundary','validation_direction','measurement_suggestion','evidence_summary')}
            quality_models=taxonomy.get('quality_models') or {};groups=('customer_experiences','quality_in_use','product_characteristics','product_subcharacteristics')
            allowed_quality={x['term_code'] if 'term_code' in x else x['code']:x for group in groups for x in quality_models.get(group,[])}
            for key in ('secondary_experience_codes','quality_in_use_codes','secondary_quality_characteristic_codes','quality_subcharacteristic_codes'):
                item[key]=list(dict.fromkeys(str(x) for x in raw.get(key,[]) if str(x) in allowed_quality))
            if item['primary_experience_code'] not in allowed_quality:item['primary_experience_code']=''
            if item['primary_quality_characteristic_code'] not in allowed_quality:item['primary_quality_characteristic_code']=''
            parents={x.get('term_code') or x.get('code'):x.get('parent_code') for x in quality_models.get('product_subcharacteristics',[])}
            selected_parents={item['primary_quality_characteristic_code'],*item['secondary_quality_characteristic_codes']}
            item['quality_subcharacteristic_codes']=[x for x in item['quality_subcharacteristic_codes'] if parents.get(x) in selected_parents]
            semantic=taxonomy.get('semantic_dictionary') or {};semantic_items=semantic.get('items') or []
            allowed_semantic={x['term_code']:x for x in semantic_items if x.get('status') in {'ACTIVE','CANDIDATE'}}
            proposed=[x for x in raw.get('proposed_semantic_terms',[]) if isinstance(x,dict)]
            proposed_codes={str(x.get('term_code') or '') for x in proposed}
            for key in ('secondary_typical_problem_codes','secondary_quality_concern_codes','environment_condition_codes'):
                item[key]=list(dict.fromkeys(str(x) for x in raw.get(key,[]) if str(x) in allowed_semantic or str(x) in proposed_codes))
            item['secondary_customer_experience_statements']=[str(x)[:160] for x in raw.get('secondary_customer_experience_statements',[]) if str(x).strip()][:5]
            for key in ('primary_typical_problem_code','primary_quality_concern_code'):
                if item[key] not in allowed_semantic and item[key] not in proposed_codes:item[key]=''
            item['proposed_semantic_terms']=proposed[:5]
            item['quality_classification_status']='PENDING_CONFIRMATION'
            item['lifecycle_code']=lifecycle;item['activity_code']=activity[0]
            item['scenario_chain']=activity[2]
            item.update({'evidence_issue_ids':evidence,'confidence':max(0,min(1,float(raw.get('confidence') or 0))),'confirmation_questions':[str(x) for x in raw.get('confirmation_questions',[]) if str(x).strip()][:5]})
            item['lifecycle_assessment']=raw.get('lifecycle_assessment') if isinstance(raw.get('lifecycle_assessment'),dict) else {}
            item['lifecycle_reason']=str(raw.get('lifecycle_reason') or '')
            item['operating_conditions']=str(raw.get('operating_conditions') or '')
            if not item['lifecycle_reason'] or len(item['lifecycle_assessment'])<3:
                item['confirmation_questions'].append('阶段比较证据不足，请核对运行执行、系统联动及长稳运行')
            source=next((x for x in payload if x.get('knowledge_id') in evidence),{})
            operation_text=' '.join(str(source.get(key) or '') for key in ('title','description'))+' '+str((source.get('itr_cs_context') or {}).get('symptom') or '')+' '+str((source.get('itr_cs_context') or {}).get('field_record') or '')
            if lifecycle=='ENGINEERING_CONFIGURATION' and re.search(r'终端正常使用|客户正常使用|正常运行阶段',context) and not re.search(r'配置|组态|编程|编译|工程创建|参数设置|下载工程|安装|升级|迁移',operation_text):
                continue
            power_activity=activities.get('POWER_LOSS_RETENTION_RECOVERY')
            if power_activity and re.search(r'掉电|断电|失电',operation_text) and re.search(r'保持|上电|恢复|丢失|清零',operation_text):
                item['lifecycle_code']=power_activity[1];item['activity_code']=power_activity[0];item['scenario_chain']=power_activity[2]
                item['lifecycle_reason']=item['lifecycle_reason'] or '证据同时包含电源中断及数据保持/上电恢复过程'
            for key in ('source_status','source_label','field_sources','field_evidence','source_warnings','source_material_id','source_material_ids','source_workbench','cs_material_id','itr_material_id','linked_knowledge_id','year','month','missing_leakage'):
                item[key]=source.get(key)
            if item['name']: items.append(item)
        return items,response.model

    def generate(self, product, start_month, end_month, created_by='WEB_USER',selected_ids=None,generation_id=''):
        records=self.source_snapshot(generation_id) or self._records(product,start_month,end_month,selected_ids)
        if not records: raise ValueError('SCENARIO_SOURCE_ANALYSIS_REQUIRED')
        taxonomy=self.scenarios.taxonomy_active(product)
        if not taxonomy:raise ValueError(f'SCENARIO_PRODUCT_TAXONOMY_NOT_ACTIVE:{product}')
        compact_taxonomy={'lifecycles':taxonomy['lifecycles'],'activities':taxonomy['activities'],'quality_models':self.scenarios.quality_models(),'semantic_dictionary':self.scenarios.semantic_dictionary(product,False)}
        parallel={'enabled':True,'max_workers':4}
        if self.ai_client is None:
            config_path=resolve_model_config_path(self.root)
            config_data=yaml.safe_load(config_path.read_text(encoding='utf-8')) or {}
            parallel.update(config_data.get('parallel_ai') or {})
        max_workers=max(1,min(16,int(parallel.get('max_workers') or 1))) if parallel.get('enabled',True) else 1
        generation_id=generation_id or f"QSG-{uuid.uuid4().hex}"
        if not self.scenarios.generation(generation_id):self.scenarios.create_generation(generation_id,product,start_month,end_month,len(records),created_by)
        self.scenarios.initialize_issue_classifications(generation_id,records)
        self.scenarios.update_generation(generation_id,status='RUNNING',taxonomy_version_id=taxonomy['version_id'],progress_text=f"正在按 {product} 场景词典 V{taxonomy['version_no']} 逐问题识别；并发数 {max_workers}")

        def analyse(index,row):
            agent_id='TEST' if self.ai_client is not None else choose_quality_issue_agent(self.root,row['knowledge_id'],slot=index)
            cfg={}
            if self.ai_client is None:cfg,_=load_quality_issue_ai_config(self.root,agent_id=agent_id)
            client=self.ai_client or OpenAICompatibleClient({**cfg,'max_tokens':max(8192,int(cfg.get('scenario_generation_max_tokens') or 8192)),'temperature':0})
            allowed={str(row['knowledge_id']):row['knowledge_id']}
            if row.get('business_issue_id'):allowed[str(row['business_issue_id'])]=row['knowledge_id']
            items,model=self._complete(client,[row],allowed,compact_taxonomy)
            return row,items[0] if items else None,agent_id,str(model or cfg.get('model') or getattr(client,'model',''))

        created=[];reused=[];updated=[];models=set();pending=[]
        for row in records:
            if row.get('source_status')=='CONFLICT':
                self.scenarios.mark_issue_classification(generation_id,row['knowledge_id'],'REVIEW_REQUIRED',error_message='同一问题存在来源冲突，请先确认数据范围',finished=True)
                continue
            input_key,evidence_hash=self.analysis_identity(row,taxonomy['version_id'],product)
            cached=self.scenarios.cached_scenario_analysis(input_key)
            if cached:
                self.scenarios.link_generation_candidate(generation_id,cached['scenario_id'])
                self.scenarios.mark_issue_classification(generation_id,row['knowledge_id'],'REUSED',scenario_id=cached['scenario_id'],activity_code=cached.get('activity_code',''),lifecycle_code=cached.get('lifecycle_code',''),agent_id='CACHE',model_name=cached.get('model_name',''),error_message='相同证据和规则已分析，直接复用',finished=True)
                reused.append(cached['scenario_id']);models.add(cached.get('model_name',''))
                continue
            canonical=row.get('canonical_itr') or row.get('business_issue_id') or row['knowledge_id']
            previous=self.scenarios.latest_scenario_analysis(canonical,'QUALITY_SCENARIO_V1',taxonomy['version_id'])
            update_scenario_id=''
            if previous:
                if previous.get('scenario_status') in {'DRAFT','IN_REVIEW'} and previous.get('quality_classification_status')!='CONFIRMED':
                    update_scenario_id=previous['scenario_id']
                else:
                    self.scenarios.link_generation_candidate(generation_id,previous['scenario_id'])
                    self.scenarios.mark_issue_classification(generation_id,row['knowledge_id'],'REVIEW_REQUIRED',scenario_id=previous['scenario_id'],error_message='发现新增证据，但已有场景已发布或人工确认；请人工评审后决定是否更新',finished=True)
                    continue
            if not self.scenarios.claim_scenario_analysis(input_key,generation_id,row['knowledge_id']):
                self.scenarios.mark_issue_classification(generation_id,row['knowledge_id'],'REVIEW_REQUIRED',error_message='相同证据正在另一个任务中分析，稍后重试即可复用',finished=True)
                continue
            pending.append((row,input_key,evidence_hash,update_scenario_id))
        tasks=[]
        for index,(row,input_key,evidence_hash,update_scenario_id) in enumerate(pending):
            agent_id='TEST' if self.ai_client is not None else choose_quality_issue_agent(self.root,row['knowledge_id'],slot=index)
            cfg={} if self.ai_client is not None else load_quality_issue_ai_config(self.root,agent_id=agent_id)[0]
            self.scenarios.mark_issue_classification(generation_id,row['knowledge_id'],'RUNNING',agent_id=agent_id,model_name=str(cfg.get('model') or getattr(self.ai_client,'model','')),started=True)
            tasks.append((index,row,input_key,evidence_hash,update_scenario_id))
        execution=ExecutionPlan(
            mode='PARALLEL' if max_workers>1 else 'SEQUENTIAL',
            max_concurrency=max_workers,
            preserve_input_order=False,
            fail_policy='CONTINUE',
            thread_name_prefix='scenario-ai',
        )
        for completed in ExecutionEngine().iter_completed(tasks,lambda task: analyse(task[0],task[1]),execution):
            index,row,input_key,evidence_hash,update_scenario_id=completed.item
            try:
                if completed.error is not None:raise completed.error
                source_row,item,agent_id,model=completed.value;models.add(model)
                if not item:
                    self.scenarios.mark_issue_classification(generation_id,row['knowledge_id'],'REVIEW_REQUIRED',agent_id=agent_id,model_name=model,error_message='AI未返回该问题的有效场景，请人工确认',finished=True)
                    raise StopIteration
                code=f"AI-{hashlib.sha1((generation_id+row['knowledge_id']).encode()).hexdigest()[:10].upper()}"
                source_records=[source_row]
                scope_fields={'IPMT':'ipmt','SPDT':'spdt','PRODUCT_MODEL':'product_model','INDUSTRY':'customer_industry','CUSTOMER_NAME':'customer_name','CUSTOMER_LEVEL':'customer_level','CUSTOMER_STATUS':'customer_status','OCCURRENCE_PHASE':'occurrence_phase'}
                scopes={kind:sorted({x['itr_cs_context'].get(field,'') for x in source_records}-{''}) for kind,field in scope_fields.items()}
                scenario_id=self.scenarios.save_generated_candidate(code,item,scopes,generation_id,product,start_month,end_month,model,update_scenario_id)
                industries=sorted({x['itr_cs_context'].get('customer_industry','') for x in source_records}-{''})
                variants=[{'industry':industry,'product_models':sorted({x['itr_cs_context'].get('product_model','') for x in source_records if x['itr_cs_context'].get('customer_industry')==industry}-{''}),'trigger_conditions':item.get('trigger_conditions',''),'business_impact':item.get('business_impact',''),'recovery_method':item.get('recovery_method',''),'evidence_count':1} for industry in industries]
                self.scenarios.save_industry_variants(scenario_id,variants)
                self.scenarios.finish_scenario_analysis(input_key,canonical_itr=row.get('canonical_itr') or row.get('business_issue_id') or row['knowledge_id'],task_type='QUALITY_SCENARIO_V1',evidence_hash=evidence_hash,taxonomy_version_id=taxonomy['version_id'],scenario_id=scenario_id,source_knowledge_id=row['knowledge_id'],model_name=model)
                status='UPDATED' if update_scenario_id else 'CLASSIFIED'
                self.scenarios.mark_issue_classification(generation_id,row['knowledge_id'],status,scenario_id=scenario_id,activity_code=item.get('activity_code',''),lifecycle_code=item.get('lifecycle_code',''),agent_id=agent_id,model_name=model,error_message='新增彻底解决单或其他有效证据已增强原候选' if update_scenario_id else '',finished=True)
                (updated if update_scenario_id else created).append(scenario_id)
            except StopIteration:
                self.scenarios.release_scenario_analysis(input_key)
            except Exception as error:
                self.scenarios.release_scenario_analysis(input_key)
                self.scenarios.mark_issue_classification(generation_id,row['knowledge_id'],'FAILED',error_message=str(error),finished=True)
            coverage=self.scenarios.refresh_generation_coverage(generation_id)
            self.scenarios.update_generation(generation_id,progress_text=f"已处理 {coverage['processed_count']}/{len(records)}；新识别 {coverage['classified_count']}，增强 {coverage['updated_count']}，复用 {coverage['reused_count']}，待确认 {coverage['review_required_count']}，失败 {coverage['failed_count']}")

        coverage=self.scenarios.refresh_generation_coverage(generation_id)
        final_status='COMPLETED' if coverage['unprocessed_count']==0 and coverage['failed_count']==0 and coverage['review_required_count']==0 else 'PARTIAL'
        text=f"覆盖 {coverage['classified_count']+coverage['reused_count']+coverage['updated_count']}/{len(records)}；新识别 {coverage['classified_count']}，增强 {coverage['updated_count']}，复用 {coverage['reused_count']}，待确认 {coverage['review_required_count']}，失败 {coverage['failed_count']}"
        model_text='、'.join(sorted(x for x in models if x))
        candidate_ids=list(dict.fromkeys([*created,*updated,*reused]))
        self.scenarios.update_generation(generation_id,status=final_status,progress_text=text,candidate_count=len(candidate_ids),model_name=model_text,error_message='' if final_status=='COMPLETED' else '部分问题未形成有效场景，请处理待确认或失败项')
        return {'generation_id':generation_id,'source_issue_count':len(records),'candidate_count':len(candidate_ids),'scenario_ids':candidate_ids,'model':model_text,**coverage,'status':final_status}

    def run_job(self,generation_id,product,start_month,end_month,selected_ids,created_by='WEB_USER'):
        try:return self.generate(product,start_month,end_month,created_by,selected_ids,generation_id)
        except Exception as error:
            self.scenarios.update_generation(generation_id,status='FAILED',progress_text='生成失败',error_message=str(error));return None

    def standardize_existing(self,scenario_ids,batch_id=''):
        models=self.scenarios.quality_models()
        allowed={x['term_code']:x for group in ('customer_experiences','quality_in_use','product_characteristics','product_subcharacteristics') for x in models[group]}
        parents={x['term_code']:x.get('parent_code') for x in models['product_subcharacteristics']}
        outcome={'completed':0,'failed':0,'skipped':0}
        def analyse(index,scenario_id):
            item=self.scenarios.scenario(scenario_id)
            if not item:raise ValueError('QUALITY_SCENARIO_NOT_FOUND')
            if item.get('quality_classification_status')=='CONFIRMED':return None
            semantics=self.scenarios.semantic_dictionary(item.get('product_code') or '',False)
            agent='TEST' if self.ai_client is not None else choose_quality_issue_agent(self.root,scenario_id,slot=index)
            cfg={} if self.ai_client is not None else load_quality_issue_ai_config(self.root,agent_id=agent)[0]
            client=self.ai_client or OpenAICompatibleClient({**cfg,'max_tokens':4096,'temperature':0})
            model=str(cfg.get('model') or getattr(client,'model',''))
            if batch_id:self.scenarios.mark_standardization_item(batch_id,scenario_id,'RUNNING',agent=agent,model=model)
            self.scenarios.mark_standardization(scenario_id,'RUNNING',agent=agent,model=model)
            fields=('name','scenario_chain','experience_requirement','concern_points','quality_attribute','quality_subcharacteristic','failure_mode','failure_mechanism','trigger_conditions','preconditions','participating_systems','system_scale','user_type','affected_object','business_impact','recovery_method','validation_direction','measurement_suggestion')
            response=client.complete([{'role':'system','content':STANDARDIZATION_PROMPT},{'role':'user','content':json.dumps({'scenario':{k:item.get(k) for k in fields},'quality_models':models,'semantic_dictionary':semantics},ensure_ascii=False)}])
            parsed,_=parse_json_object(response.content,allow_repair=True)
            if not isinstance(parsed,dict):raise ValueError('QUALITY_STANDARDIZATION_SCHEMA_INVALID')
            result={'customer_perception':str(parsed.get('customer_perception') or '')[:160]}
            for key in ('primary_experience_code','primary_quality_characteristic_code'):
                code=str(parsed.get(key) or '');result[key]=code if code in allowed else ''
            for key in ('secondary_experience_codes','quality_in_use_codes','secondary_quality_characteristic_codes','quality_subcharacteristic_codes'):
                result[key]=list(dict.fromkeys(str(x) for x in parsed.get(key,[]) if str(x) in allowed))
            semantic_allowed={x['term_code']:x for x in semantics['items']}
            for key in ('primary_typical_problem_code','primary_quality_concern_code'):
                code=str(parsed.get(key) or '');result[key]=code if code in semantic_allowed else ''
            for key in ('secondary_typical_problem_codes','secondary_quality_concern_codes','environment_condition_codes'):
                result[key]=list(dict.fromkeys(str(x) for x in parsed.get(key,[]) if str(x) in semantic_allowed))
            for key in ('primary_customer_experience_statement','operating_environment','operating_condition','duration_frequency','disturbances','extreme_conditions'):
                result[key]=str(parsed.get(key) or '')[:240]
            result['secondary_customer_experience_statements']=[str(x)[:160] for x in parsed.get('secondary_customer_experience_statements',[]) if str(x).strip()][:5]
            selected={result['primary_quality_characteristic_code'],*result['secondary_quality_characteristic_codes']}
            result['quality_subcharacteristic_codes']=[x for x in result['quality_subcharacteristic_codes'] if parents.get(x) in selected]
            if not result['primary_experience_code'] or not result['primary_quality_characteristic_code']:raise ValueError('QUALITY_STANDARDIZATION_REQUIRED_CODE_MISSING')
            return scenario_id,agent,str(response.model or model),result
        ids=list(dict.fromkeys(scenario_ids))
        tasks=list(enumerate(ids))
        execution=ExecutionPlan(
            mode='PARALLEL' if len(ids)>1 else 'SEQUENTIAL',
            max_concurrency=min(8,max(1,len(ids))),
            preserve_input_order=False,
            fail_policy='CONTINUE',
            thread_name_prefix='scenario-standardize',
        )
        for completed in ExecutionEngine().iter_completed(tasks,lambda task: analyse(task[0],task[1]),execution):
            index,sid=completed.item
            try:
                if completed.error is not None:raise completed.error
                result=completed.value
                if result is None:outcome['skipped']+=1;continue
                scenario_id,agent,model,payload=result
                self.scenarios.save_standardization(scenario_id,payload,agent=agent,model=model)
                if batch_id:self.scenarios.mark_standardization_item(batch_id,scenario_id,'COMPLETED',agent=agent,model=model)
                outcome['completed']+=1
            except Exception as error:
                try:self.scenarios.mark_standardization(sid,'FAILED',error=str(error))
                except Exception:pass
                if batch_id:self.scenarios.mark_standardization_item(batch_id,sid,'FAILED',error=str(error))
                outcome['failed']+=1

        if batch_id:
            for sid in ids:
                item=next((x for x in self.scenarios.standardization_batch(batch_id)['items'] if x['scenario_id']==sid),None)
                if item and item['status']=='PENDING':self.scenarios.mark_standardization_item(batch_id,sid,'SKIPPED')
        return outcome