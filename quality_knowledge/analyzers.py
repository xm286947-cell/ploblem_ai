from __future__ import annotations
import hashlib, json
from pathlib import Path
from typing import Any, Type
from pydantic import BaseModel, ValidationError
from builder.ai_client import OpenAICompatibleClient, AIClientError
from builder.json_response import parse_json_object
from quality_knowledge.models.analysis import OccurrenceAnalysisDTO, EscapeAnalysisDTO, RecurrenceRiskDTO, CapabilityGapDTO
from quality_knowledge.model_config import load_quality_issue_ai_config, validate_quality_issue_ai_config, ModelConfigError
from quality_knowledge.response_normalizer import normalize_stage_response


class StageAnalysisError(RuntimeError):
    def __init__(self, stage: str, cause: Exception, debug: dict[str, Any]):
        super().__init__(f'{stage} analysis failed: {cause}')
        self.stage = stage
        self.cause = cause
        self.debug = debug


class StageAnalyzer:
    def __init__(self, root: str|Path, stage: str, dto: Type[BaseModel], *, client=None, agent_id=''):
        self.root=Path(root); self.stage=stage; self.dto=dto
        self.ai_cfg, self.model_config_path = load_quality_issue_ai_config(self.root,agent_id=agent_id,stage=stage)
        self.agent_id=self.ai_cfg.get('_agent_id','DEFAULT')
        stage_runtime = dict((self.ai_cfg.get('stage_runtime') or {}).get(stage) or {})
        self.ai_cfg = {**self.ai_cfg, **stage_runtime}
        if client is None:
            check = validate_quality_issue_ai_config(self.root, require_enabled=True,agent_id=agent_id,stage=stage)
            if not check['ok']:
                raise ModelConfigError('; '.join(check['errors']) + f"; config={check['config_path']}")
        self.prompt_path=self.root/f'quality_knowledge/prompts/{stage}.md'
        self.prompt=self.prompt_path.read_text(encoding='utf-8')
        self.prompt_version=hashlib.sha256(self.prompt_path.read_bytes()).hexdigest()[:12]
        self.client=client or OpenAICompatibleClient(self.ai_cfg)

    def analyze(self, payload: dict[str,Any]):
        messages=[{'role':'system','content':self.prompt},{'role':'user','content':json.dumps(payload,ensure_ascii=False)}]
        last=None
        last_debug={'stage':self.stage,'attempt':0,'raw_response':None,'parsed_json':None,'normalized_json':None,'validation_error':None}
        validation_retries=max(0,int(self.ai_cfg.get('validation_retries',1)))
        for attempt in range(1, validation_retries + 2):
            debug={'stage':self.stage,'attempt':attempt,'raw_response':None,'parsed_json':None,'normalized_json':None,'validation_error':None,'input_chars':len(messages[-1]['content']),'output_chars':0,'max_tokens':int(self.ai_cfg.get('max_tokens',4096)),'finish_reason':None,'response_truncated':False}
            try:
                r=self.client.complete(messages)
                debug['raw_response']=r.content
                debug['output_chars']=len(r.content)
                if isinstance(getattr(r,'raw',None),dict):
                    choices=r.raw.get('choices') or []
                    debug['finish_reason']=(choices[0].get('finish_reason') if choices else None)
                    debug['usage']=r.raw.get('usage')
                    debug['response_truncated']=debug['finish_reason']=='length'
                obj,_=parse_json_object(r.content,allow_repair=True)
                debug['parsed_json']=obj
                normalized=normalize_stage_response(self.stage,obj)
                debug['normalized_json']=normalized
                if self.stage=='capability_gap':
                    max_per_dimension=max(1,int(self.ai_cfg.get('max_items_per_dimension',3)))
                    selected=[]
                    counts={}
                    for x in normalized.get('capability_gaps',[]):
                        dim=str(x.get('dimension') or 'TECHNICAL').upper()
                        if counts.get(dim,0) >= max_per_dimension:
                            continue
                        selected.append(x); counts[dim]=counts.get(dim,0)+1
                    priority_order={'P0':0,'P1':1,'P2':2}
                    selected.sort(key=lambda x:priority_order.get(str(x.get('priority') or 'P2').upper(),2))
                    normalized['capability_gaps']=selected
                    result=[CapabilityGapDTO.model_validate(x) for x in selected]
                else:
                    result=self.dto.model_validate(normalized)
                return result, r.model, debug
            except (AIClientError,ValidationError,ValueError,RuntimeError) as e:
                last=e
                debug['validation_error']=str(e)
                last_debug=debug
                messages.append({'role':'user','content':'上一次输出未通过结构校验或可能过长。请重新输出更短的严格JSON，不要Markdown或解释；只保留最关键结论，字段名必须遵循系统JSON模板。待确认问题最多3条；Capability Gap全部合计最多5项。'})
        raise StageAnalysisError(self.stage,last or RuntimeError('unknown error'),last_debug)

class OccurrenceAnalyzer(StageAnalyzer):
    def __init__(self,root,client=None,agent_id=''): super().__init__(root,'occurrence',OccurrenceAnalysisDTO,client=client,agent_id=agent_id)
class EscapeAnalyzer(StageAnalyzer):
    def __init__(self,root,client=None,agent_id=''): super().__init__(root,'escape',EscapeAnalysisDTO,client=client,agent_id=agent_id)
class RecurrenceAnalyzer(StageAnalyzer):
    def __init__(self,root,client=None,agent_id=''): super().__init__(root,'recurrence',RecurrenceRiskDTO,client=client,agent_id=agent_id)
class CapabilityGapAnalyzer(StageAnalyzer):
    def __init__(self,root,client=None,agent_id=''): super().__init__(root,'capability_gap',CapabilityGapDTO,client=client,agent_id=agent_id)
