from __future__ import annotations
import hashlib,json,uuid
from pathlib import Path
from typing import Any
from quality_knowledge.analyzers import OccurrenceAnalyzer,EscapeAnalyzer,RecurrenceAnalyzer,CapabilityGapAnalyzer

ENGINE_VERSION='QUALITY_KNOWLEDGE_M2'
class QualityIssueAnalysisService:
    def __init__(self,repo,root: str|Path,*,client=None,include_customer_name=False,include_sensitive_notes=False):
        self.repo=repo; self.root=Path(root); self.include_customer_name=include_customer_name; self.include_sensitive_notes=include_sensitive_notes
        self.analyzers={
          'occurrence':OccurrenceAnalyzer(root,client), 'escape':EscapeAnalyzer(root,client),
          'recurrence':RecurrenceAnalyzer(root,client), 'capability_gap':CapabilityGapAnalyzer(root,client)}
    def _context(self,kid):
        c=self.repo.get_analysis_context(kid)
        if not c: raise KeyError(f'knowledge_id not found: {kid}')
        if not self.include_customer_name: c['customer']=''
        raw=c.pop('raw_json',{})
        if self.include_sensitive_notes: c['raw_selected']=raw
        return c
    def _run_stage(self,kid,stage,payload):
        a=self.analyzers[stage]; run_id='qar-'+uuid.uuid4().hex
        inp=json.dumps(payload,ensure_ascii=False,sort_keys=True,default=str)
        self.repo.start_analysis_run({'analysis_run_id':run_id,'knowledge_id':kid,'analysis_type':stage,'model_provider':a.ai_cfg.get('provider','openai_compatible'),'prompt_name':a.prompt_path.name,'prompt_version':a.prompt_version,'schema_version':'1.0.0','engine_version':ENGINE_VERSION,'status':'RUNNING','input_hash':hashlib.sha256(inp.encode()).hexdigest()})
        try:
            result,model,debug=a.analyze(payload); dump=[x.model_dump(mode='json') for x in result] if isinstance(result,list) else result.model_dump(mode='json')
            self.repo.save_analysis(kid,run_id,stage,{'capability_gaps':dump} if stage=='capability_gap' else dump)
            if stage=='capability_gap': self.repo.save_capability_gaps(kid,run_id,result,model,a.prompt_version)
            self.repo.finish_analysis_run(run_id,'SUCCESS',model); return dump
        except Exception as e:
            self.repo.finish_analysis_run(run_id,'FAILED',error_message=str(e)); raise
    def analyze_one(self,kid,*,only_missing=False,overwrite=False):
        c=self._context(kid); out={'knowledge_id':kid,'status':'SUCCESS','stages':{}}
        prior={}
        for stage in ('occurrence','escape','recurrence','capability_gap'):
            existing=self.repo.latest_analysis(kid,stage)
            if only_missing and existing and not overwrite:
                val=existing['result']; val=val.get('capability_gaps',[]) if stage=='capability_gap' else val; out['stages'][stage]={'status':'SKIPPED_EXISTING','result':val}; prior[stage]=val; continue
            payload={'issue':c}
            if stage in ('recurrence','capability_gap'): payload['occurrence_analysis']=prior.get('occurrence') or (self.repo.latest_analysis(kid,'occurrence') or {}).get('result',{}) ; payload['escape_analysis']=prior.get('escape') or (self.repo.latest_analysis(kid,'escape') or {}).get('result',{})
            if stage=='capability_gap': payload['recurrence_analysis']=prior.get('recurrence') or (self.repo.latest_analysis(kid,'recurrence') or {}).get('result',{})
            try: val=self._run_stage(kid,stage,payload); prior[stage]=val; out['stages'][stage]={'status':'SUCCESS','result':val}
            except Exception as e: out['stages'][stage]={'status':'FAILED','error':str(e)}; out['status']='PARTIAL_FAILED'; break
        return out
    def analyze_batch(self,knowledge_ids):
        results=[]
        for kid in knowledge_ids:
            try: results.append(self.analyze_one(kid))
            except Exception as e: results.append({'knowledge_id':kid,'status':'FAILED','error':str(e)})
        return {'total':len(results),'success':sum(x['status']=='SUCCESS' for x in results),'items':results}
