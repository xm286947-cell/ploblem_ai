from __future__ import annotations
import hashlib,json,re,uuid,os
from pathlib import Path
from collections import Counter
from typing import Any
from quality_knowledge.product_report.service import ProductReportError
from quality_knowledge.web.statistics_presenter import zh_value
from quality_knowledge.model_config import load_quality_issue_ai_config
from builder.ai_client import OpenAICompatibleClient
from builder.json_response import parse_json_object

class LegacyProductQualityReportService:
    """Report adapter for the stable knowledge-web workflow."""
    def __init__(self, issue_service: Any, root: str|Path|None=None, ai_client=None):
        self.issues=issue_service;self.repository=issue_service.repository;self.root=Path(root) if root else Path(__file__).resolve().parents[2];self.ai_client=ai_client
        with self.repository.connect() as c:c.executescript("""CREATE TABLE IF NOT EXISTS product_quality_report(report_id TEXT PRIMARY KEY,product_code TEXT NOT NULL,start_month TEXT NOT NULL,end_month TEXT NOT NULL,status TEXT NOT NULL,current_version_no INTEGER NOT NULL DEFAULT 1,created_by TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP,updated_at TEXT DEFAULT CURRENT_TIMESTAMP);CREATE TABLE IF NOT EXISTS product_quality_report_version(report_version_id TEXT PRIMARY KEY,report_id TEXT NOT NULL,version_no INTEGER NOT NULL,status TEXT NOT NULL,scope_hash TEXT NOT NULL,report_json TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP,UNIQUE(report_id,version_no));CREATE TABLE IF NOT EXISTS product_quality_report_batch_cache(batch_hash TEXT PRIMARY KEY,agent_id TEXT NOT NULL,model_name TEXT,summary_json TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP);""")
    @staticmethod
    def _month(value):
        m=re.search(r"\d+",str(value or ""));return int(m.group()) if m else -1
    def _scope(self,product,start,end):
        lo,hi=self._month(start),self._month(end);rows=self.issues.query_issues({'business_type':product} if product else {},100000)
        return [x for x in rows if lo<=self._month(x.get('month'))<=hi]
    def precheck(self,product,start,end):
        rows=self._scope(product,start,end);ids=[x['knowledge_id'] for x in rows];metrics=self.issues.get_workspace_metrics(product or None,ids);total=len(rows);done=int(metrics.get('analyzed_count') or 0)
        return {'product_code':product,'start_month':start,'end_month':end,'issue_count':total,'completed_count':done,'analysed_count':done,'analysis_coverage_rate':round(done*100/total,1) if total else 0,'ready':bool(total and done),'limitations':(['当前范围内没有问题数据'] if not total else [])+(['当前范围没有已完成的单问题 AI 摘要，无法生成产品级结论'] if total and not done else [])+(['部分问题尚未完成 AI 分析，报告会明确标注覆盖率'] if done and done<total else [])}
    def create(self,product,start,end,created_by=''):
        check=self.precheck(product,start,end)
        if not check['issue_count']:raise ProductReportError('REPORT_SCOPE_EMPTY')
        if not check['ready']:raise ProductReportError('PRODUCT_REPORT_AI_SUMMARY_REQUIRED')
        rows=self._scope(product,start,end);ids=[x['knowledge_id'] for x in rows];stats=self.issues.get_statistics(product or None,50,ids);runtime=self.issues.get_workspace_metrics(product or None,ids);report=self._compose(check,stats,runtime,rows);report.update(self._ai_synthesis(product,start,end,rows));rid=f'PQR-{uuid.uuid4().hex}';vid=f'PQRV-{uuid.uuid4().hex}';digest=hashlib.sha256('|'.join(sorted(ids)).encode()).hexdigest()
        with self.repository.connect() as c:c.execute('INSERT INTO product_quality_report(report_id,product_code,start_month,end_month,status,created_by) VALUES(?,?,?,?,?,?)',(rid,product,start,end,'REVIEW_REQUIRED',created_by));c.execute('INSERT INTO product_quality_report_version(report_version_id,report_id,version_no,status,scope_hash,report_json) VALUES(?,?,?,?,?,?)',(vid,rid,1,'REVIEW_REQUIRED',digest,json.dumps(report,ensure_ascii=False)));c.commit()
        return self.get(rid)
    def list(self):
        with self.repository.connect() as c:rows=c.execute('SELECT * FROM product_quality_report ORDER BY created_at DESC,report_id DESC').fetchall()
        return [dict(x) for x in rows]
    def get(self,rid):
        with self.repository.connect() as c:row=c.execute('SELECT r.*,v.report_json,v.scope_hash FROM product_quality_report r JOIN product_quality_report_version v ON v.report_id=r.report_id AND v.version_no=r.current_version_no WHERE r.report_id=?',(rid,)).fetchone()
        if not row:raise ProductReportError('REPORT_NOT_FOUND')
        out=dict(row);out['report']=json.loads(out.pop('report_json'));return out
    def publish(self,rid):
        current=self.get(rid)
        if current.get('report',{}).get('synthesis_status')!='COMPLETED':raise ProductReportError('PRODUCT_REPORT_AI_SYNTHESIS_REQUIRED')
        with self.repository.connect() as c:
            if not c.execute('SELECT 1 FROM product_quality_report WHERE report_id=?',(rid,)).fetchone():raise ProductReportError('REPORT_NOT_FOUND')
            c.execute("UPDATE product_quality_report SET status='PUBLISHED',updated_at=CURRENT_TIMESTAMP WHERE report_id=?",(rid,));c.execute("UPDATE product_quality_report_version SET status='PUBLISHED' WHERE report_id=?",(rid,));c.commit()
        return self.get(rid)
    def delete(self,rid):
        current=self.get(rid)
        if current.get('status')=='PUBLISHED':raise ProductReportError('PUBLISHED_REPORT_CANNOT_BE_DELETED')
        with self.repository.connect() as c:
            c.execute('BEGIN IMMEDIATE');c.execute('DELETE FROM product_quality_report_version WHERE report_id=?',(rid,));c.execute('DELETE FROM product_quality_report WHERE report_id=?',(rid,));c.commit()
        return {'report_id':rid,'deleted':True}
    def _compose(self,check,stats,runtime,rows):
        def axis(key,name):return [{'capability_axis':name,'capability_code':str(x.get('category') or ''),'capability_label_zh':zh_value(x.get('category')),'issue_count':int(x.get('count') or 0),'score':round(int(x.get('count') or 0)*100/max(len(rows),1),1),'control_status_distribution':{}} for x in (stats.get(key) or [])[:3]]
        eng=axis('top_technical_gaps','QUALITY_ENGINEERING');manage=axis('top_management_gaps','QUALITY_MANAGEMENT');cand=sorted(eng+manage,key=lambda x:(-x['score'],-x['issue_count']))[:3];contr=[{'rank':i+1,'name':x['capability_label_zh'],'axis':x['capability_axis'],'risk_score':x['score'],'issue_count':x['issue_count'],'control_status':{},'judgement':'该类能力缺口重复出现，应结合关联问题证据建立可验证的工程或管理控制。'} for i,x in enumerate(cand)];sev=Counter(str(x.get('severity') or 'UNKNOWN').upper() for x in rows)
        return {'schema_version':'PRODUCT_QUALITY_REPORT_MVP_V1','scope':check,'overall_judgement':f"本范围共 {len(rows)} 个问题，AI 完成覆盖率 {check['analysis_coverage_rate']}%。建议优先治理重复出现且控制不足的关键矛盾。",'risk_summary':{'severity_distribution':dict(sev),'high_risk_count':int(runtime.get('high_risk_count') or sev.get('HIGH',0)+sev.get('H',0)),'primary_mrc_top5':[]},'engineering_quality_summary':eng,'quality_management_summary':manage,'core_contradictions':contr,'governance_priorities':[{'priority':'P0','action':'复核高风险问题并补齐遏制措施','validation':'高风险问题均有措施和验证证据'},{'priority':'P1','action':'针对 TOP3 矛盾修复流程或工程机制','validation':'同类问题新增率和流出率下降'},{'priority':'P2','action':'沉淀标准、工具和横向复制机制','validation':'控制覆盖相关产品与阶段'}],'manual_confirmation_questions':['TOP3 矛盾是否符合业务实际？','高风险问题是否已有有效遏制措施？','治理责任角色和验证周期是否明确？'],'evidence_issues':[{'knowledge_id':x['knowledge_id'],'business_issue_id':x.get('business_issue_id')} for x in rows[:50]],'limitations':check['limitations']}
    @staticmethod
    def _value(obj,key):
        value=(obj or {}).get(key)
        return value.get('value') if isinstance(value,dict) else value
    @staticmethod
    def _short(value,limit=180):
        text=re.sub(r'\s+',' ',str(value or '')).strip()
        return text if len(text)<=limit else text[:limit]+'…'
    @staticmethod
    def _required_keys():
        return ('executive_summary','problem_landscape','occurrence_diagnosis','escape_diagnosis','engineering_capability_gaps','management_capability_gaps','core_contradictions')
    def _clean_synthesis(self,parsed,allowed):
        if not isinstance(parsed,dict) or not all(key in parsed for key in self._required_keys()):raise ValueError('PRODUCT_REPORT_AI_SCHEMA_INVALID')
        for key in ('problem_landscape','occurrence_diagnosis','escape_diagnosis','engineering_capability_gaps','management_capability_gaps','core_contradictions'):
            cleaned=[]
            for raw in parsed.get(key,[]):
                if not isinstance(raw,dict):continue
                item=dict(raw);item['evidence_issue_ids']=[x for x in item.get('evidence_issue_ids',[]) if x in allowed][:20]
                if item['evidence_issue_ids']:cleaned.append(item)
            parsed[key]=cleaned[:8] if key!='core_contradictions' else cleaned[:3]
        parsed['manual_confirmation_questions']=[str(x) for x in parsed.get('manual_confirmation_questions',[]) if str(x).strip()][:8]
        return parsed
    def _save_report_raw(self,user_content,response,attempt):
        digest=hashlib.sha256(user_content.encode('utf-8')).hexdigest()[:20]
        folder=self.root/'output'/'raw_ai'/'product_report';folder.mkdir(parents=True,exist_ok=True)
        raw_path=folder/f'{digest}.attempt{attempt}.txt';raw_path.write_text(str(response.content or ''),encoding='utf-8')
        raw=getattr(response,'raw',None) or {};choices=raw.get('choices') or [{}]
        meta={'attempt':attempt,'raw_path':str(raw_path),'output_chars':len(str(response.content or '')),'finish_reason':choices[0].get('finish_reason'),'usage':raw.get('usage')}
        (folder/f'{digest}.attempt{attempt}.meta.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
        return meta
    def _complete(self,client,prompt,payload,allowed):
        user_content=json.dumps(payload,ensure_ascii=False,default=str)
        messages=[{'role':'system','content':prompt},{'role':'user','content':user_content}]
        response=client.complete(messages)
        first_meta=self._save_report_raw(user_content,response,1)
        try:
            parsed,_=parse_json_object(response.content,allow_repair=True)
            return self._clean_synthesis(parsed,allowed),response.model
        except Exception as first_error:
            retry_prompt=prompt+'\n\n上一次响应被截断或不是完整 JSON。请重新完整输出；每个数组最多 5 项，每个文字字段不超过 120 个汉字，不要 Markdown、解释或代码围栏。'
            retry=client.complete([{'role':'system','content':retry_prompt},{'role':'user','content':user_content}])
            retry_meta=self._save_report_raw(user_content,retry,2)
            try:
                parsed,_=parse_json_object(retry.content,allow_repair=True)
                return self._clean_synthesis(parsed,allowed),retry.model
            except Exception as retry_error:
                raise ValueError(f'PRODUCT_REPORT_JSON_INVALID: first_chars={first_meta["output_chars"]}, first_finish={first_meta["finish_reason"]}, first={first_error}; retry_chars={retry_meta["output_chars"]}, retry_finish={retry_meta["finish_reason"]}, retry={retry_error}') from retry_error
    def _cached_batch(self,batch_hash):
        with self.repository.connect() as c:row=c.execute('SELECT summary_json,model_name FROM product_quality_report_batch_cache WHERE batch_hash=?',(batch_hash,)).fetchone()
        return (json.loads(row['summary_json']),row['model_name']) if row else None
    def _save_batch(self,batch_hash,agent_id,model_name,summary):
        with self.repository.connect() as c:c.execute('INSERT OR REPLACE INTO product_quality_report_batch_cache(batch_hash,agent_id,model_name,summary_json) VALUES(?,?,?,?)',(batch_hash,agent_id,model_name,json.dumps(summary,ensure_ascii=False)));c.commit()
    @staticmethod
    def _dimension_records(dimension,summaries):
        rows=[]
        for item in summaries:
            base={'knowledge_id':item['knowledge_id'],'title':item.get('title'),'severity':item.get('severity'),'month':item.get('month')}
            if dimension=='PROBLEM':data={**base,'recurrence':item.get('recurrence')}
            elif dimension=='OCCURRENCE':data={**base,'occurrence':item.get('occurrence')}
            elif dimension=='ESCAPE':data={**base,'escape':item.get('escape')}
            else:
                management=dimension=='MANAGEMENT';gaps=[]
                for gap in item.get('capability_gaps') or []:
                    kind=str(gap.get('dimension') or '').upper();is_management=('MANAGEMENT' in kind or 'GOVERNANCE' in kind)
                    if is_management==management:gaps.append(gap)
                data={**base,'capability_gaps':gaps}
                if not gaps:continue
            if any(v for k,v in data.items() if k not in ('knowledge_id','title','severity','month')):rows.append(data)
        return rows
    @staticmethod
    def _clean_dimension(parsed,dimension,allowed):
        if not isinstance(parsed,dict) or not isinstance(parsed.get('items'),list):raise ValueError('PRODUCT_REPORT_DIMENSION_SCHEMA_INVALID')
        cleaned=[]
        for raw in parsed['items']:
            if not isinstance(raw,dict):continue
            item=dict(raw);item['evidence_issue_ids']=[x for x in item.get('evidence_issue_ids',[]) if x in allowed][:20]
            if not item['evidence_issue_ids']:continue
            item['issue_count']=len(set(item['evidence_issue_ids']));cleaned.append(item)
        return {'dimension':dimension,'items':cleaned[:5]}
    def _complete_dimension(self,client,prompt,payload,dimension,allowed):
        user_content=json.dumps(payload,ensure_ascii=False,default=str);response=client.complete([{'role':'system','content':prompt},{'role':'user','content':user_content}]);first_meta=self._save_report_raw(user_content,response,1)
        try:
            parsed,_=parse_json_object(response.content,allow_repair=True);return self._clean_dimension(parsed,dimension,allowed),response.model
        except Exception as first_error:
            retry=client.complete([{'role':'system','content':prompt+'\n上次JSON无效。请压缩内容并完整输出，每个字段不超过80字。'},{'role':'user','content':user_content}]);retry_meta=self._save_report_raw(user_content,retry,2)
            try:
                parsed,_=parse_json_object(retry.content,allow_repair=True);return self._clean_dimension(parsed,dimension,allowed),retry.model
            except Exception as retry_error:raise ValueError(f'PRODUCT_REPORT_{dimension}_JSON_INVALID: first_chars={first_meta["output_chars"]}, retry_chars={retry_meta["output_chars"]}, first={first_error}, retry={retry_error}') from retry_error
    def _dimension_synthesis(self,client,prompt,dimension,records,agent_id,batch_size):
        if not records:return {'dimension':dimension,'items':[]},None,0,0
        chunks=[records[i:i+batch_size] for i in range(0,len(records),batch_size)];maps=[];model=None;cache_hits=0
        for index,chunk in enumerate(chunks,1):
            digest=hashlib.sha256(json.dumps({'prompt':'DIMENSION_V1','agent':agent_id,'dimension':dimension,'items':chunk},ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest();cached=self._cached_batch(digest)
            if cached:result,model=cached;cache_hits+=1
            else:
                allowed={x['knowledge_id'] for x in chunk};payload={'mode':'DIMENSION_MAP','dimension':dimension,'batch_no':index,'batch_count':len(chunks),'records':chunk};result,model=self._complete_dimension(client,prompt,payload,dimension,allowed);self._save_batch(digest,agent_id,model,result)
            maps.append(result)
        if len(maps)==1:return maps[0],model,len(chunks),cache_hits
        allowed={x['knowledge_id'] for x in records};payload={'mode':'DIMENSION_REDUCE','dimension':dimension,'batch_count':len(maps),'batch_results':maps};result,model=self._complete_dimension(client,prompt,payload,dimension,allowed)
        return result,model,len(chunks),cache_hits
    def _complete_executive(self,client,prompt,dimensions,allowed):
        payload={'mode':'EXECUTIVE_FINAL','dimensions':dimensions};user_content=json.dumps(payload,ensure_ascii=False,default=str);response=client.complete([{'role':'system','content':prompt},{'role':'user','content':user_content}]);self._save_report_raw(user_content,response,1);parsed,_=parse_json_object(response.content,allow_repair=True)
        if not isinstance(parsed,dict) or 'executive_summary' not in parsed or not isinstance(parsed.get('core_contradictions'),list):raise ValueError('PRODUCT_REPORT_EXECUTIVE_SCHEMA_INVALID')
        contradictions=[]
        for raw in parsed['core_contradictions']:
            if not isinstance(raw,dict):continue
            item=dict(raw);item['evidence_issue_ids']=[x for x in item.get('evidence_issue_ids',[]) if x in allowed][:20]
            if item['evidence_issue_ids']:contradictions.append(item)
        parsed['core_contradictions']=contradictions[:3];parsed['manual_confirmation_questions']=[str(x) for x in parsed.get('manual_confirmation_questions',[]) if str(x).strip()][:5]
        return parsed,response.model
    @staticmethod
    def _dimensions_to_report(dimensions):
        by={x['dimension']:x.get('items',[]) for x in dimensions}
        def convert(key):
            out=[]
            for x in by.get(key,[]):
                common={'issue_count':x.get('issue_count'),'evidence_issue_ids':x.get('evidence_issue_ids',[])}
                if key=='PROBLEM':out.append({**common,'theme':x.get('title'),'summary':x.get('judgement'),'customer_impact':x.get('impact')})
                elif key=='OCCURRENCE':out.append({**common,'cause':x.get('title'),'mechanism':x.get('mechanism') or x.get('judgement')})
                elif key=='ESCAPE':out.append({**common,'escape_reason':x.get('title'),'expected_detection':x.get('expected_detection'),'control_failure':x.get('control_gap') or x.get('judgement')})
                else:out.append({**common,'gap':x.get('title'),'impact':x.get('impact') or x.get('judgement')})
            return out
        return {'problem_landscape':convert('PROBLEM'),'occurrence_diagnosis':convert('OCCURRENCE'),'escape_diagnosis':convert('ESCAPE'),'engineering_capability_gaps':convert('ENGINEERING'),'management_capability_gaps':convert('MANAGEMENT')}
    def _ai_synthesis(self,product,start,end,rows):
        summaries=[]
        for row in rows:
            latest={name:self.repository.get_latest_analysis(row['knowledge_id'],name) for name in ('occurrence','escape','recurrence','capability_gap')}
            stages={name:(latest[name] or {}).get('result') or {} for name in latest}
            gaps=stages['capability_gap'].get('capability_gaps',[]) if isinstance(stages['capability_gap'],dict) else []
            summaries.append({'has_analysis':any(latest.values()),'knowledge_id':row['knowledge_id'],'business_issue_id':row.get('business_issue_id'),'title':self._short(row.get('title'),100),'severity':row.get('severity'),'month':row.get('month'),'occurrence':{'root_cause':self._short(self._value(stages['occurrence'],'root_cause_summary')),'failure_mechanism':self._short(self._value(stages['occurrence'],'failure_mechanism'),140),'category':stages['occurrence'].get('occurrence_category'),'mrc':(stages['occurrence'].get('mrc') or {}).get('label_zh')},'escape':{'reason':self._short(self._value(stages['escape'],'escape_cause_summary')),'verification_gap':self._short(self._value(stages['escape'],'verification_gap'),140),'process_gap':self._short(self._value(stages['escape'],'process_gap'),140),'expected_detection':stages['escape'].get('expected_detection_stage'),'missing_control':self._short(stages['escape'].get('missing_control'),120)},'recurrence':{'risk':stages['recurrence'].get('recurrence_risk_level'),'reason':self._short(self._value(stages['recurrence'],'recurrence_risk_reason'),140),'customer_impact':self._short(stages['recurrence'].get('customer_impact'),140)},'capability_gaps':[{'dimension':x.get('dimension'),'category':x.get('category'),'description':self._short(x.get('description'),120),'why_needed':self._short(x.get('why_needed'),100)} for x in gaps[:4]]})
        usable=[x for x in summaries if x.pop('has_analysis',False)]
        if not usable:return {'synthesis_status':'NO_AI_SUMMARY','synthesis_message':'范围内没有可用于产品级综合的单问题 AI 摘要。','product_ai_synthesis':{}}
        try:
            agent_id='DEFAULT'
            cfg,_=load_quality_issue_ai_config(self.root,agent_id=agent_id)
            report_max_tokens=max(8192,int(cfg.get('product_report_max_tokens') or cfg.get('max_tokens') or 4096))
            client=self.ai_client or OpenAICompatibleClient({**cfg,'max_tokens':report_max_tokens,'temperature':0})
            dimension_prompt=(self.root/'quality_knowledge/prompts/product_quality_report_dimension.md').read_text(encoding='utf-8');executive_prompt=(self.root/'quality_knowledge/prompts/product_quality_report_executive.md').read_text(encoding='utf-8')
            allowed={x['knowledge_id'] for x in usable};batch_size=max(5,min(30,int(os.getenv('PRODUCT_REPORT_DIMENSION_BATCH_SIZE','20'))));dimensions=[];batch_count=0;cache_hits=0;model=str(cfg.get('model') or '')
            for dimension in ('PROBLEM','OCCURRENCE','ESCAPE','ENGINEERING','MANAGEMENT'):
                records=self._dimension_records(dimension,usable);result,used_model,batches,hits=self._dimension_synthesis(client,dimension_prompt,dimension,records,agent_id,batch_size);dimensions.append(result);batch_count+=batches;cache_hits+=hits;model=used_model or model
            executive,model=self._complete_executive(client,executive_prompt,dimensions,allowed);report_sections=self._dimensions_to_report(dimensions);report_sections.update({'executive_summary':executive['executive_summary'],'core_contradictions':executive['core_contradictions'],'manual_confirmation_questions':executive.get('manual_confirmation_questions',[])})
            return {'synthesis_status':'COMPLETED','synthesis_strategy':'DIMENSIONAL_MAP_REDUCE','synthesis_dimension_count':5,'synthesis_batch_count':batch_count,'synthesis_batch_size':batch_size,'synthesis_cache_hits':cache_hits,'synthesis_agent':agent_id,'synthesis_model':model,'synthesis_issue_count':len(usable),'product_ai_synthesis':report_sections,'overall_judgement':executive['executive_summary'],'core_contradictions':executive['core_contradictions'],'manual_confirmation_questions':executive.get('manual_confirmation_questions',[])}
        except Exception as error:
            return {'synthesis_status':'FAILED','synthesis_message':str(error),'synthesis_issue_count':len(usable),'product_ai_synthesis':{}}
