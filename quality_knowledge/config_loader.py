from __future__ import annotations
import re
from pathlib import Path
from typing import Any
import yaml

_BASE = Path(__file__).resolve().parent / 'config'


def _load_yaml(name: str) -> dict[str, Any]:
    path = _BASE / name
    if not path.exists():
        return {}
    with path.open('r', encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


def import_config() -> dict[str, Any]:
    return _load_yaml('import.yaml')


def business_config(business_type: str) -> dict[str, Any]:
    return _load_yaml(f'{business_type.lower()}_fields.yaml')


def normalize_header(value: Any) -> str:
    if value is None:
        return ''
    text = str(value).strip()
    # normalize common unicode punctuation/spacing first
    text = text.replace('\u3000', ' ')
    text = text.replace('（', '(').replace('）', ')')
    text = text.replace('：', ':')
    # remove human-maintenance hints such as (×开发填写×)
    text = re.sub(r'\([^()]*[×✕xX][^()]*[×✕xX][^()]*\)', '', text)
    text = re.sub(r'【[^】]*】', '', text)
    # field matching ignores whitespace/newlines/tabs
    text = re.sub(r'\s+', '', text)
    return text.strip()


def issue_id_aliases(business_type: str) -> tuple[str, ...]:
    cfg = business_config(business_type)
    values = (cfg.get('identity') or {}).get('business_issue_id', {}).get('aliases') or []
    return tuple(dict.fromkeys(normalize_header(x) for x in values if normalize_header(x)))


def detection_weights(business_type: str) -> dict[str, int]:
    cfg = business_config(business_type)
    fields = (cfg.get('detection') or {}).get('characteristic_fields') or {}
    return {normalize_header(k): int(v) for k, v in fields.items() if normalize_header(k)}


def canonical_alias_map(business_type: str) -> dict[str, str]:
    '''normalized alias -> canonical target leaf name, for diagnostics only.'''
    cfg = business_config(business_type)
    out: dict[str, str] = {}
    for section, content in cfg.items():
        if section in {'version','business_type','status','detection'} or not isinstance(content, dict):
            continue
        for target, spec in content.items():
            if isinstance(spec, dict):
                for alias in spec.get('aliases') or []:
                    out[normalize_header(alias)] = f'{section}.{target}'
    return out


def field_mapping_diagnostics(business_type: str, source_headers) -> dict[str, Any]:
    from quality_knowledge.models.issue import IssueFact,ProductContext,OccurrenceFact,EscapeFact,SolutionFact,VerificationFact
    typed={
      'issue_fact':set(IssueFact.model_fields),'product_context':set(ProductContext.model_fields),
      'occurrence':set(OccurrenceFact.model_fields),'escape':set(EscapeFact.model_fields),
      'solution':set(SolutionFact.model_fields),'verification':set(VerificationFact.model_fields),
      'identity':{'business_issue_id'},
    }
    amap=canonical_alias_map(business_type)
    rows=[]; structured=extension=raw_only=unmatched=0
    for raw in source_headers:
        if raw is None or not str(raw).strip(): continue
        canonical=normalize_header(raw); target=amap.get(canonical)
        if target:
            section,leaf=(target.split('.',1)+[''])[:2]
            if section in {'extension','recurrence'} or (section in typed and leaf not in typed[section]):
                status='MATCHED_EXTENSION'; domain='Product Extension'; extension+=1
            else:
                status='MATCHED_STRUCTURED'; domain=section; structured+=1
        else:
            status='RAW_ONLY'; domain='Original Raw'; raw_only+=1; unmatched+=1
        rows.append({'source_header':str(raw),'canonical_header':canonical,'target_field':target or '', 'target_domain':domain,'mapping_status':status})
    total=len(rows); coverage=round((structured+extension)*100/total,2) if total else 100.0
    return {'business_type':business_type,'total_source_fields':total,'matched_structured':structured,'matched_extension':extension,'raw_only':raw_only,'unmatched':unmatched,'coverage_percent':coverage,'fields':rows,'unmatched_fields':[x['source_header'] for x in rows if x['mapping_status']=='RAW_ONLY']}
