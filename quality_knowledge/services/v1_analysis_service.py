from __future__ import annotations
import hashlib,json,uuid
from pathlib import Path
from quality_knowledge.analyzers import OccurrenceAnalyzer,EscapeAnalyzer,RecurrenceAnalyzer,CapabilityGapAnalyzer,StageAnalysisError
from quality_knowledge.analysis_profiles import normalize_analysis_profile
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
    def __init__(self,repository,root: str|Path,client=None):
        self.repository=repository
        self.analyzers={'occurrence':OccurrenceAnalyzer(root,client),'escape':EscapeAnalyzer(root,client),'recurrence':RecurrenceAnalyzer(root,client),'capability_gap':CapabilityGapAnalyzer(root,client)}
        self.stage_timeouts={k:int(v.ai_cfg.get('timeout_seconds',120)) for k,v in self.analyzers.items()}
        if hasattr(self.repository,'expire_stale_analysis_runs'):
            self.repository.expire_stale_analysis_runs(self.stage_timeouts,buffer_seconds=60)

    def _run(self,kid,vid,stage,payload,analysis_profile):
        a=self.analyzers[stage];rid='QAR-'+uuid.uuid4().hex;raw=json.dumps(payload,ensure_ascii=False,sort_keys=True,default=str)
        self.repository.start_analysis_run({'analysis_run_id':rid,'knowledge_id':kid,'issue_version_id':vid,'analysis_type':stage,'model_provider':a.ai_cfg.get('provider','openai_compatible'),'prompt_name':a.prompt_path.name,'prompt_version':a.prompt_version,'schema_version':'1.0.0','engine_version':ENGINE_VERSION,'analysis_profile':analysis_profile,'status':'RUNNING','input_hash':hashlib.sha256(raw.encode()).hexdigest()})
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
        out={'knowledge_id':kid,'issue_version_id':vid,'status':'COMPLETED','force':bool(force),'analysis_profile':profile,'human_confirmation_count':len(human_confirmations),'stages':{}}
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

    def run_batch_analysis(self,ids,only_missing=None,force=False,analysis_profile=None):
        items=[]
        for kid in ids:
            try:items.append(self.run_issue_analysis(kid,only_missing=only_missing,force=force,analysis_profile=analysis_profile))
            except Exception as e:items.append({'knowledge_id':kid,'status':'FAILED','error':str(e)})
        return {'total':len(items),'completed':sum(x['status']=='COMPLETED' for x in items),'failed':sum(x['status']=='FAILED' for x in items),'force':bool(force),'analysis_profile':normalize_analysis_profile(analysis_profile),'items':items}
