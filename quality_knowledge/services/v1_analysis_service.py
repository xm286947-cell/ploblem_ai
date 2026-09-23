from __future__ import annotations
import hashlib,json,uuid,time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from quality_knowledge.analyzers import OccurrenceAnalyzer,EscapeAnalyzer,RecurrenceAnalyzer,CapabilityGapAnalyzer,StageAnalysisError
from quality_knowledge.analysis_profiles import normalize_analysis_profile
from quality_knowledge.model_config import choose_quality_issue_agent,load_quality_issue_ai_config
ENGINE_VERSION='KNOWLEDGE_QUALITY_ISSUE_ANALYSIS_ENGINE_V1.0_ADDENDUM01_AI_PATCH03'

STAGES=('occurrence','escape','recurrence','capability_gap')

def _truncate(value, max_chars=1800):
    if isinstance(value,str):
        return value if len(value)<=max_chars else value[:max_chars]+'...[truncated]'
    if isinstance(value,list):
        return [_truncate(x,max_chars) for x in value[:30]]
    if isinstance(value,dict):
        return {k:_truncate(v,max_chars) for k,v in value.items()}
    return value

def _slim_issue_context(c, stage):
    identity={k:c.get(k) for k in ('knowledge_id','issue_version_id','business_type','business_issue_id') if c.get(k) is not None}
    if stage in ('occurrence','escape'):
        # First-stage analyzers still need the complete normalized business context.
        return _truncate(c,2400)
    keep=('issue_fact','product_context','solution','verification','recurrence')
    out=dict(identity)
    for k in keep:
        if k in c and c.get(k) not in (None,{},[],''):
            out[k]=_truncate(c[k],1400)
    # Compatibility with flatter normalized schemas.
    for k in ('title','summary','description','impact','problem_description','solution','verification_result','existing_action'):
        if k in c and k not in out and c.get(k) not in (None,''):
            out[k]=_truncate(c[k],1400)
    return out

class KnowledgeIssueAnalysisService:
    def __init__(self,repository,root: str|Path,client=None,agent_id=''):
        self.repository=repository;self.root=Path(root);self.client=client
        self.agent_id=str(agent_id or '')
        self.analyzers={} if not self.agent_id else {'occurrence':OccurrenceAnalyzer(root,client,agent_id=self.agent_id),'escape':EscapeAnalyzer(root,client,agent_id=self.agent_id),'recurrence':RecurrenceAnalyzer(root,client,agent_id=self.agent_id),'capability_gap':CapabilityGapAnalyzer(root,client,agent_id=self.agent_id)}
        if self.analyzers:
            self.stage_timeouts={k:int(v.ai_cfg.get('timeout_seconds',120)) for k,v in self.analyzers.items()}
        else:
            cfg,_=load_quality_issue_ai_config(root,agent_id='DEFAULT')
            self.stage_timeouts={stage:int(dict((cfg.get('stage_runtime') or {}).get(stage) or {}).get('timeout_seconds',cfg.get('timeout_seconds',120))) for stage in STAGES}
        if hasattr(self.repository,'expire_stale_analysis_runs'):
            self.repository.expire_stale_analysis_runs(self.stage_timeouts,buffer_seconds=60)

    def _run(self,kid,vid,stage,payload,analysis_profile):
        a=self.analyzers[stage];rid='QAR-'+uuid.uuid4().hex;raw=json.dumps(payload,ensure_ascii=False,sort_keys=True,default=str)
        audit_profile={**analysis_profile,'analysis_agent':a.agent_id}
        self.repository.start_analysis_run({'analysis_run_id':rid,'knowledge_id':kid,'issue_version_id':vid,'analysis_type':stage,'model_provider':a.ai_cfg.get('provider','openai_compatible'),'prompt_name':a.prompt_path.name,'prompt_version':a.prompt_version,'schema_version':'1.0.0','engine_version':ENGINE_VERSION,'analysis_profile':audit_profile,'status':'RUNNING','input_hash':hashlib.sha256(raw.encode()).hexdigest()})
        try:
            result,model,debug=a.analyze(payload)
            self.repository.save_analysis_debug(rid,stage,debug)
            data=[x.model_dump(mode='json') for x in result] if isinstance(result,list) else result.model_dump(mode='json');stored={'capability_gaps':data} if stage=='capability_gap' else data
            self.repository.save_analysis_result(kid,vid,rid,stage,stored)
            if stage=='capability_gap':self.repository.replace_capability_gaps(kid,vid,rid,result)
            self.repository.finish_analysis_run(rid,'COMPLETED',model_name=model);return {'run_id':rid,'result':data}
        except StageAnalysisError as e:
            self.repository.save_analysis_debug(rid,stage,e.debug)
            self.repository.finish_analysis_run(rid,'FAILED',error_message=str(e));raise
        except Exception as e:
            self.repository.save_analysis_debug(rid,stage,{'stage':stage,'validation_error':str(e)})
            self.repository.finish_analysis_run(rid,'FAILED',error_message=str(e));raise

    def run_issue_analysis(self,kid,only_missing=None,force=False,analysis_profile=None):
        if not self.agent_id:
            selected=choose_quality_issue_agent(self.root,kid)
            return KnowledgeIssueAnalysisService(self.repository,self.root,self.client,agent_id=selected).run_issue_analysis(kid,only_missing=only_missing,force=force,analysis_profile=analysis_profile)
        c=self.repository.get_analysis_context(kid)
        if not c:raise KeyError(kid)
        requested=dict(analysis_profile or {})
        issue_fact=c.get('issue_fact') or {}
        issue_domain=str(issue_fact.get('issue_domain') or c.get('issue_domain') or 'AUTO').upper()
        if str(requested.get('domain_profile') or 'AUTO').upper() == 'AUTO' and issue_domain != 'AUTO':
            requested['domain_profile']=issue_domain
            requested.setdefault('selection_source', 'ISSUE_ATTRIBUTE')
        profile=normalize_analysis_profile(requested);vid=c['issue_version_id'];prior={}
        human_confirmations=self.repository.get_human_confirmations(kid) if hasattr(self.repository,'get_human_confirmations') else []
        out={'knowledge_id':kid,'issue_version_id':vid,'status':'COMPLETED','force':bool(force),'analysis_profile':profile,'analysis_agent':self.agent_id or 'DYNAMIC','human_confirmation_count':len(human_confirmations),'stages':{}}
        for stage in STAGES:
            old=self.repository.get_latest_analysis(kid,stage)
            # New default: completed result for current Issue Version is reusable.
            old_profile={}
            if old and old.get('analysis_profile_json'):
                try: old_profile=json.loads(old['analysis_profile_json'])
                except (TypeError,ValueError): old_profile={}
            profile_changed=bool(analysis_profile) and normalize_analysis_profile(old_profile)!=profile
            if old and not force and not profile_changed:
                val=old['result'].get('capability_gaps',[]) if stage=='capability_gap' else old['result']
                prior[stage]=val
                out['stages'][stage]={'status':'SKIPPED_COMPLETED','run_id':old.get('analysis_run_id'),'result':val}
                continue

            payload={'issue':_slim_issue_context(c,stage),'analysis_profile':profile,'human_confirmations':human_confirmations}
            if stage in ('recurrence','capability_gap'):
                payload['occurrence_analysis']=prior.get('occurrence') or (self.repository.get_latest_analysis(kid,'occurrence') or {}).get('result',{})
                payload['escape_analysis']=prior.get('escape') or (self.repository.get_latest_analysis(kid,'escape') or {}).get('result',{})
            if stage=='capability_gap':
                payload['recurrence_analysis']=prior.get('recurrence') or (self.repository.get_latest_analysis(kid,'recurrence') or {}).get('result',{})

            try:
                r=self._run(kid,vid,stage,payload,profile);prior[stage]=r['result'];out['stages'][stage]={'status':'COMPLETED',**r}
            except Exception as e:
                out['status']='FAILED';out['stages'][stage]={'status':'FAILED','error':str(e),'timeout_seconds':self.stage_timeouts.get(stage)};break
        return out

    def incomplete_issue_ids(self,ids):
        """Return only issues missing at least one completed stage for the current version."""
        unique_ids=list(dict.fromkeys(str(kid) for kid in ids if kid))
        return [kid for kid in unique_ids if any(
            not self.repository.get_latest_analysis(kid,stage) for stage in STAGES
        )]

    def run_batch_analysis(self,ids,only_missing=None,force=False,analysis_profile=None,concurrency=1,progress_callback=None):
        requested_ids=list(dict.fromkeys(str(kid) for kid in ids if kid))
        ids=(self.incomplete_issue_ids(requested_ids) if only_missing and not force else requested_ids)
        skipped_completed=len(requested_ids)-len(ids)
        workers=max(1,min(int(concurrency or 1),4))
        def analyze(index_and_kid):
            index,kid=index_and_kid
            started=time.monotonic()
            try:
                if getattr(self,'agent_id','') or not hasattr(self,'root'):
                    item=self.run_issue_analysis(kid,only_missing=only_missing,force=force,analysis_profile=analysis_profile)
                else:
                    selected=choose_quality_issue_agent(self.root,kid,slot=index)
                    item=KnowledgeIssueAnalysisService(self.repository,self.root,self.client,agent_id=selected).run_issue_analysis(kid,only_missing=only_missing,force=force,analysis_profile=analysis_profile)
            except Exception as error:
                item={'knowledge_id':kid,'status':'FAILED','error':str(error)}
            item['duration_ms']=round((time.monotonic()-started)*1000)
            if item.get('status')=='FAILED' and not item.get('error'):
                failed_stage=next((name for name,value in (item.get('stages') or {}).items() if value.get('status')=='FAILED'),None)
                if failed_stage:
                    item['failed_stage']=failed_stage
                    item['error']=((item.get('stages') or {}).get(failed_stage) or {}).get('error')
            if progress_callback:
                progress_callback(item)
            return item
        if workers == 1:
            items=[analyze(pair) for pair in enumerate(ids)]
        else:
            with ThreadPoolExecutor(max_workers=workers,thread_name_prefix='quality-ai') as executor:
                items=list(executor.map(analyze,enumerate(ids)))
        return {'total':len(items),'requested_total':len(requested_ids),'skipped_completed':skipped_completed,'completed':sum(x['status']=='COMPLETED' for x in items),'failed':sum(x['status']=='FAILED' for x in items),'force':bool(force),'concurrency':workers,'analysis_agent':getattr(self,'agent_id','') or 'DYNAMIC','analysis_profile':normalize_analysis_profile(analysis_profile),'items':items}
