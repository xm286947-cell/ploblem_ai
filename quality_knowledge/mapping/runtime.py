from __future__ import annotations
from quality_knowledge.config_loader import normalize_header

DOMAIN_SECTION={
 'ISSUE_FACT':'issue_fact','PRODUCT_CONTEXT':'product_context','OCCURRENCE':'occurrence',
 'ESCAPE':'escape','SOLUTION':'solution','VERIFICATION':'verification','RECURRENCE':'recurrence',
 'PRODUCT_EXTENSION':'extension'
}

class MappingNotInitializedError(RuntimeError): pass

def effective_to_adapter_config(config: dict) -> dict:
    if not config:
        raise MappingNotInitializedError('MAPPING_NOT_INITIALIZED')
    out={'version':config['version'],'business_type':config['business_type'],'status':config['status']}
    for item in config.get('mappings') or []:
        if not item.get('enabled',True): continue
        section=DOMAIN_SECTION.get(item.get('target_domain'))
        if not section: continue
        target=item.get('target_field') or item.get('canonical_field')
        # Legacy identity mapping is represented in A2 DB as ISSUE_FACT.business_issue_id.
        if target=='business_issue_id': section='identity'
        aliases=[]
        for x in (item.get('source_headers') or [])+(item.get('aliases') or []):
            if x and x not in aliases: aliases.append(x)
        out.setdefault(section,{})[target]={
            'aliases':aliases,'required':bool(item.get('required')),'enabled':True,
            'description':item.get('description') or ''}
    return out

def runtime_aliases(config: dict, target='business_issue_id') -> tuple[str,...]:
    cfg=effective_to_adapter_config(config)
    return tuple(dict.fromkeys(normalize_header(x) for x in (cfg.get('identity',{}).get(target,{}) or {}).get('aliases',[]) if normalize_header(x)))

def runtime_detection_score(config: dict, headers) -> int:
    hs={normalize_header(x) for x in headers if normalize_header(x)}
    aliases=set(runtime_aliases(config))
    all_aliases=set()
    for item in config.get('mappings') or []:
        if not item.get('enabled',True): continue
        all_aliases.update(normalize_header(x) for x in (item.get('source_headers') or [])+(item.get('aliases') or []) if normalize_header(x))
    ordinary=(hs & all_aliases)-aliases
    return len(hs & aliases)*20 + len(ordinary)*5

def runtime_mapping_diagnostics(config:dict, source_headers):
    rows=[]; structured=extension=unmatched=0
    index={}
    for item in config.get('mappings') or []:
        if not item.get('enabled',True): continue
        for x in (item.get('source_headers') or [])+(item.get('aliases') or []):
            k=normalize_header(x)
            if k and item not in index.setdefault(k,[]): index[k].append(item)
    for raw in source_headers:
        if raw is None or not str(raw).strip():continue
        k=normalize_header(raw); matches=index.get(k,[])
        if len(matches)==1:
            i=matches[0]; ext=i['target_domain']=='PRODUCT_EXTENSION'; status='MATCHED_EXTENSION' if ext else 'MATCHED_STRUCTURED'
            extension+=int(ext);structured+=int(not ext)
            tf=('identity.business_issue_id' if i['target_field']=='business_issue_id' else i['target_field']); rows.append({'source_header':str(raw),'canonical_header':k,'target_field':tf,'target_domain':i['target_domain'],'mapping_status':status})
        elif len(matches)>1:
            unmatched+=1;rows.append({'source_header':str(raw),'canonical_header':k,'target_field':'','target_domain':'','mapping_status':'CONFLICT'})
        else:
            unmatched+=1;rows.append({'source_header':str(raw),'canonical_header':k,'target_field':'','target_domain':'Original Raw','mapping_status':'UNMATCHED'})
    total=len(rows);return {'business_type':config['business_type'],'mapping_config_id':config['config_id'],'mapping_config_version':config['version'],'total_source_fields':total,'matched_structured':structured,'matched_extension':extension,'raw_only':unmatched,'unmatched':unmatched,'coverage_percent':round((structured+extension)*100/total,2) if total else 100.0,'fields':rows,'unmatched_fields':[x['source_header'] for x in rows if x['mapping_status'] in {'UNMATCHED','CONFLICT'}]}
