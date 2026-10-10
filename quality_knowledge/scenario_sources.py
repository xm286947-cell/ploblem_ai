"""Software-operation selection, with explicit field-level evidence provenance."""
import json
import re
from collections import defaultdict
from quality_knowledge.materials import normalize_itr, year_from_itr, effective_reporting_year

LABELS = {'LEAKAGE':'有漏测分析', 'PARTIAL':'漏测分析不完整，已补充彻底解决单',
          'CS_ONLY':'无漏测分析，基于彻底解决单', 'ITR_ONLY':'仅有ITR现场信息，根因待确认',
          'INSUFFICIENT':'依据不足，待补充'}

CONTEXT_ALIASES = {
    'ipmt':('问题信息_IPMT','IPMT'),'spdt':('问题信息_SPDT','SPDT'),
    'product_model':('问题信息_产品型号','产品型号'),'product_code':('问题信息_产品编码','产品编码'),
    'product_type':('问题信息_产品类型','产品类型'),'product_line':('问题信息_产品线','产品线'),
    'product_series':('问题信息_产品系列','产品系列'),'software_version':('问题信息_软件版本号','软件版本号'),
    'customer_industry':('问题信息_客户行业','客户行业'),'customer_name':('问题信息_客户名称','客户名称'),
    'customer_level':('问题信息_客户分级','问题信息_客户吸引力','客户分级'),
    'customer_status':('问题信息_当前客户状态','问题信息_当前问题状态','当前客户状态','当前问题状态'),
    'occurrence_phase':('问题信息_问题发生阶段','问题发生阶段'),
    'root_cause':('问题信息_问题原因定位','问题原因定位'),
    'trc_correction':('技术根因分析与纠正_TRC纠正信息','TRC纠正信息'),
    'trc_root_cause':('技术根因分析与纠正_TRC根因','技术根因分析与纠正_根因分析','TRC根因'),
    'solution':('问题处理结果_问题解决方案','技术根因分析与纠正_解决方案','解决方案'),
    'equipment_code':('问题信息_设备编码','设备编码'),'equipment_name':('问题信息_设备名称','设备名称'),
    'terminal_name':('问题信息_终端名称','终端名称'),'occurrence_location':('问题信息_问题发生地点','问题发生地点'),
    'occurrence_region':('问题信息_问题发生地区归属','问题发生地区归属'),
    'used_duration':('问题信息_已用时长','已用时长'),'failure_count':('问题信息_故障台数','故障台数'),
    'failure_frequency':('问题信息_不良问题频率','不良问题频率'),
    'symptom':('问题信息_故障现象描述','故障现象描述'),'symptom_tag':('问题信息_故障现象标签','故障现象标签'),
    'initial_judgement':('问题信息_已做排查及初步判断','已做排查及初步判断'),
    'field_record':('恢复措施执行_现场作业记录','现场作业记录'),
    'recovery_measure':('问题处理结果_问题解决方案','恢复措施','问题解决方案'),
    'issue_domain':('问题信息_问题领域','问题领域','技术根因分析与纠正_问题类型'),
    'component_failure':('技术根因分析与纠正_是否器件失效','是否器件失效'),
    'component_category':('技术根因分析与纠正_器件类别_单板级','技术根因分析与纠正_器件类别','器件类别'),
    'circuit_category':('技术根因分析与纠正_功能电路大类','功能电路大类'),
    'circuit_subcategory':('技术根因分析与纠正_功能电路小类','功能电路小类'),
    'component_refdes':('技术根因分析与纠正_位号','位号'),
    'component_code':('技术根因分析与纠正_编码','器件编码'),
    'component_vendor':('技术根因分析与纠正_厂家','器件厂家'),
    'component_failure_mode':('技术根因分析与纠正_器件失效模式','器件失效模式'),
    'component_failure_mechanism':('技术根因分析与纠正_器件失效机理','器件失效机理'),
    'mechanical_failure_mode':('技术根因分析与纠正_产品层级机械失效模式','产品层级机械失效模式'),
    'mechanical_mechanism':('技术根因分析与纠正_部件层级机械机理/根因','部件层级机械机理/根因'),
    'software_module':('技术根因分析与纠正_软件模块','软件模块'),
    'software_function':('技术根因分析与纠正_软件功能','软件功能'),
    'software_failure_mode':('技术根因分析与纠正_产品层级软件失效模式','产品层级软件失效模式'),
    'software_failure_mechanism':('技术根因分析与纠正_功能层级软件失效机理','功能层级软件失效机理'),
}

def first(raw, *keys):
    return next((str(raw[k]).strip() for k in keys if raw.get(k) not in (None, '', [], {})), '')

def period(raw,business_key='',reporting_year='',year_source=''):
    value=first(raw,'数据运营_KPI计入月份','KPI计入月份')
    year=effective_reporting_year(raw,business_key,reporting_year,year_source) or '未知'
    dated=re.search(r'(20\d{2})\s*(?:年|[-/.])\s*(\d{1,2})',value)
    if dated:return year,str(int(dated[2]))
    month=re.fullmatch(r'\s*(\d{1,2})\s*月?\s*',value)
    return year or '未知', str(int(month[1])) if month and 1<=int(month[1])<=12 else '未知'

def context_from(raw):
    return {key:first(raw,*names) for key,names in CONTEXT_ALIASES.items()}

def normalize_problem_domain(value, context=None, software_operation=False):
    """Use explicit structured evidence first; never infer the domain from free text."""
    if software_operation:return 'SOFTWARE'
    text=str(value or '').strip().upper()
    if any(x in text for x in ('SOFTWARE','软件')):return 'SOFTWARE'
    if any(x in text for x in ('MECHANICAL','机械')):return 'MECHANICAL'
    if any(x in text for x in ('HARDWARE','硬件','器件','电子')):return 'HARDWARE'
    if '结构' in text:return 'MECHANICAL'
    context=context or {}
    if any(context.get(x) for x in ('software_module','software_function','software_failure_mode','software_failure_mechanism')):return 'SOFTWARE'
    if any(context.get(x) for x in ('mechanical_failure_mode','mechanical_mechanism')):return 'MECHANICAL'
    if any(context.get(x) for x in ('component_failure','component_category','circuit_category','circuit_subcategory','component_refdes','component_code','component_vendor','component_failure_mode','component_failure_mechanism')):return 'HARDWARE'
    return 'UNKNOWN'

def source_product(context):
    """Actual source-data product scope; independent from the chosen scenario taxonomy."""
    return next((context.get(key,'') for key in ('product_type','product_line','product_series','product_code') if context.get(key)), '')

def portrait_period(raw, business_key=''):
    for key,label in (('问题信息_问题发生时间','问题发生时间'),('问题信息_提单时间','提单时间')):
        value=first(raw,key,label)
        match=re.search(r'(20\d{2})\D{0,3}(\d{1,2})',value)
        if match and 1<=int(match[2])<=12:return match[1],str(int(match[2])),key
    value=first(raw,'问题信息_创建月份','创建月份')
    match=re.search(r'(20\d{2})\D{0,3}(\d{1,2})',value)
    if match and 1<=int(match[2])<=12:return match[1],str(int(match[2])),'问题信息_创建月份'
    month=re.fullmatch(r'\s*(\d{1,2})\s*月?\s*',value)
    return year_from_itr(business_key) or '未知',str(int(month[1])) if month and 1<=int(month[1])<=12 else '未知','问题信息_创建月份' if value else 'UNKNOWN'

def _latest_materials(service, material_types):
    marks=','.join('?' for _ in material_types)
    with service.scenarios.connect() as c:
        rows=[dict(r) for r in c.execute(f"SELECT m.*,g.group_code FROM source_material m JOIN data_group g ON g.group_id=m.group_id WHERE m.material_type IN ({marks}) ORDER BY m.version_no DESC,m.created_at DESC,m.material_id",tuple(material_types))]
    latest={}
    for row in rows:latest.setdefault((row['material_type'],row['group_id'],normalize_itr(row['canonical_itr'] or row['business_key'])),row)
    return list(latest.values())

def _issue_index(service):
    with service.scenarios.connect() as c:
        try:
            rows=[dict(r) for r in c.execute('''SELECT q.knowledge_id,q.business_issue_id,q.business_type,
                        v.issue_domain,v.product AS issue_product
                    FROM quality_issue q
                    LEFT JOIN quality_issue_version v ON v.issue_version_id=q.current_version_id''')]
        except Exception:
            try:rows=[dict(r) for r in c.execute('SELECT knowledge_id,business_issue_id,business_type FROM quality_issue')]
            except Exception:rows=[]
    result=defaultdict(list)
    for row in rows:result[normalize_itr(row['business_issue_id'])].append(row)
    return result

def _analyses(service, matches, product_code=''):
    if len(matches)>1 and product_code:
        narrowed=[row for row in matches if row.get('business_type')==product_code]
        if narrowed:matches=narrowed
    issue=matches[0] if len(matches)==1 else None; analyses={}
    if issue:
        for stage in ('occurrence','escape','recurrence','capability_gap'):
            latest=service.issues.get_latest_analysis(issue['knowledge_id'],stage) or {}
            if latest.get('result'):analyses[stage]=latest['result']
    return issue,analyses

def material_scene_records(service, filters=None, selected_ids=None, metadata_only=False, include_analysis=True):
    """One controlled scene input per canonical ITR; CS facts win, ITR only fills gaps."""
    filters=filters or {};selected=set(selected_ids or [])
    rows=_latest_materials(service,('ITR_CS','ITR_SOURCE'))
    group_ids=filters.get('group_ids') or []
    if isinstance(group_ids,str):group_ids=[x for x in group_ids.split(',') if x]
    if group_ids:
        allowed_groups=set(group_ids);rows=[row for row in rows if row['group_id'] in allowed_groups]
    by_itr=defaultdict(lambda:defaultdict(list))
    for row in rows:by_itr[normalize_itr(row['canonical_itr'] or row['business_key'])][row['material_type']].append(row)
    issue_index=_issue_index(service);records=[]
    for canonical,bucket in sorted(by_itr.items()):
        cs_rows=bucket['ITR_CS'];itr_rows=bucket['ITR_SOURCE']
        primary=cs_rows[0] if len(cs_rows)==1 else itr_rows[0] if len(itr_rows)==1 else None
        selectable=primary or (cs_rows+itr_rows)[0]
        if selected and selectable['material_id'] not in selected:continue
        if len(cs_rows)>1 or (not cs_rows and len(itr_rows)>1):
            if metadata_only:continue
            records.append({'knowledge_id':selectable['material_id'],'business_issue_id':selectable['business_key'],'canonical_itr':canonical,
                'title':'来源数据冲突，待人工选择','description':'','source_status':'CONFLICT','source_label':'同一数据类型存在多个数据组版本',
                'source_warnings':['关联来源冲突，未自动选取'],'source_material_id':selectable['material_id'],'source_workbench':'cs' if selectable['material_type']=='ITR_CS' else 'itr',
                'year':'未知','month':'未知','ipmt':'','spdt':'','product_model':'','leakage_analysis':{},'itr_cs_context':{},'field_sources':{},'source_hashes':[x['source_hash'] for x in cs_rows+itr_rows]})
            continue
        cs=cs_rows[0] if cs_rows else None;itr=itr_rows[0] if itr_rows else None
        cs_raw=json.loads(cs['raw_json']) if cs else {};itr_raw=json.loads(itr['raw_json']) if itr else {}
        cs_context=context_from(cs_raw);itr_context=context_from(itr_raw)
        context={key:cs_context.get(key) or itr_context.get(key) or '' for key in CONTEXT_ALIASES}
        year,month,time_source=portrait_period(cs_raw or itr_raw,(cs or itr)['business_key'])
        linked_issues=issue_index[canonical]
        problem_domain=normalize_problem_domain(context.get('issue_domain'),context)
        linked_domains={normalize_problem_domain(x.get('issue_domain')) for x in linked_issues}-{'UNKNOWN'}
        if problem_domain=='UNKNOWN' and len(linked_domains)==1:problem_domain=linked_domains.pop()
        actual_product=source_product(context)
        linked_products={str(x.get('issue_product') or '').strip() for x in linked_issues}-{''}
        if not actual_product and len(linked_products)==1:actual_product=linked_products.pop()
        values={key:context.get(key,'') for key in ('ipmt','spdt','product_model')}
        values.update({'industry':context.get('customer_industry',''),'customer':context.get('customer_name','')})
        values.update({'year':year,'month':month,'problem_domain':problem_domain,'source_product':actual_product})
        if any(filters.get(key) and str(filters[key])!=str(value) for key,value in values.items()):continue
        if (filters.get('start_month') or filters.get('end_month')) and (month=='未知' or not service._month(filters.get('start_month') or '1')<=int(month)<=service._month(filters.get('end_month') or '12')):continue
        if metadata_only:records.append(values);continue
        issue,analyses=_analyses(service,linked_issues,filters.get('product_code','')) if include_analysis else (linked_issues[0] if len(linked_issues)==1 else None,{})
        root=service._value(analyses.get('occurrence',{}),'root_cause_summary','root_cause')
        escape=service._value(analyses.get('escape',{}),'escape_cause_summary','escape_reason')
        facts=cs_raw or itr_raw
        description=first(facts,'问题信息_问题描述','问题信息_问题主题','问题描述') or context['symptom']
        label='LEAKAGE' if root and escape else 'PARTIAL' if analyses and cs else 'CS_ONLY' if cs else 'ITR_ONLY'
        warnings=[]
        if len(issue_index[canonical])>1:warnings.append('关联到多个漏测问题，未自动选择分析结论')
        if not analyses:warnings.append('无漏测分析')
        if not cs:warnings.append('仅有ITR现场信息，原因定位和测试漏测结论待确认')
        sources=cs_rows+itr_rows
        field_sources={key:'ITR_CS' if cs_context.get(key) else 'ITR_SOURCE' if itr_context.get(key) else 'MISSING' for key in context}
        field_evidence={key:{'source':field_sources[key],'value':value,'material_id':cs['material_id'] if field_sources[key]=='ITR_CS' and cs else itr['material_id'] if field_sources[key]=='ITR_SOURCE' and itr else ''} for key,value in context.items() if value}
        field_sources.update({'root_cause':'LEAKAGE' if root else field_sources.get('root_cause','MISSING'),'escape_reason':'LEAKAGE' if escape else 'MISSING','time':time_source})
        records.append({'knowledge_id':selectable['material_id'],'business_issue_id':selectable['business_key'],'canonical_itr':canonical,
            'title':description or '问题描述待补充','description':description,**values,'severity':first(facts,'问题信息_问题等级','问题等级'),
            'source_status':label,'source_label':LABELS[label],'source_warnings':warnings,'source_material_id':selectable['material_id'],
            'source_workbench':'cs' if cs else 'itr','cs_material_id':cs['material_id'] if cs else '', 'itr_material_id':itr['material_id'] if itr else '',
            'linked_knowledge_id':issue['knowledge_id'] if issue else '','field_sources':field_sources,'field_evidence':field_evidence,'leakage_analysis':analyses,
            'occurrence':{'root_cause':root or context['root_cause'] or context['trc_root_cause']},'escape':{'reason':escape},
            'itr_cs_context':context,'supplemental_context':context,'missing_leakage':not bool(analyses),
            'source_hashes':sorted(x['source_hash'] for x in sources),'source_material_ids':[x['material_id'] for x in sources]})
    return records

def scene_source_records(service, source='operations', filters=None, selected_ids=None, metadata_only=False):
    if source=='operations':return operation_records(service,filters,selected_ids,metadata_only)
    if source=='cs_itr':return material_scene_records(service,filters,selected_ids,metadata_only)
    raise ValueError('SCENARIO_SOURCE_INVALID')

def operation_records(service, filters=None, selected_ids=None, metadata_only=False):
    filters=filters or {}; selected=set(selected_ids or [])
    with service.scenarios.connect() as c:
        # Selection is deliberately driven only by the software-operation workbench.
        # CS/ITR and issue analyses enrich the selected rows later and must never
        # reduce the number of selectable operation issues.
        materials=[dict(r) for r in c.execute('''WITH ranked AS (
                SELECT m.*,y.reporting_year,y.year_source,
                       ROW_NUMBER() OVER (
                         PARTITION BY m.group_id,COALESCE(NULLIF(m.canonical_itr,''),m.business_key)
                         ORDER BY m.version_no DESC,m.created_at DESC,m.material_id DESC
                       ) AS rn
                FROM source_material m
                JOIN data_group g ON g.group_id=m.group_id
                LEFT JOIN source_material_reporting_year y ON y.material_id=m.material_id
                WHERE m.material_type='SOFTWARE_OPERATION' AND g.group_code='SW-OPS'
            ) SELECT * FROM ranked WHERE rn=1 ORDER BY business_key,material_id''')]
        issues=[dict(r) for r in c.execute('SELECT knowledge_id,business_issue_id,business_type FROM quality_issue')]
    latest={}; cs=defaultdict(list); index=defaultdict(list)
    for row in materials:latest[(row['material_type'],row['group_id'],normalize_itr(row['business_key']))]=row
    for row in issues:index[normalize_itr(row['business_issue_id'])].append(row)
    # Metadata/choices and filtered counts need no CS join.  For the full rows,
    # load latest CS records separately so a missing/conflicting CS cannot hide
    # a software-operation record.
    if not metadata_only:
        for row in _latest_materials(service,('ITR_CS',)):
            cs[normalize_itr(row['business_key'])].append(row)
    records=[]
    for material in latest.values():
        if material['material_type']!='SOFTWARE_OPERATION' or (selected and material['material_id'] not in selected):continue
        raw=json.loads(material['raw_json'])
        if material.get('reporting_year'):raw['数据运营_KPI计入年份']=material['reporting_year']
        year,month=period(raw,material['business_key'],material.get('reporting_year'),material.get('year_source'))
        operation_context=context_from(raw)
        values={'ipmt':first(raw,'问题信息_IPMT','IPMT'),'spdt':first(raw,'问题信息_SPDT','SPDT'),
                'product_model':first(raw,'问题信息_产品型号','产品型号'),
                'product_series':first(raw,'问题信息_产品系列','产品系列'),
                'industry':first(raw,'问题信息_客户行业','客户行业'),
                'customer':first(raw,'问题信息_客户名称','客户名称'),'year':year,'month':month,
                'problem_domain':'SOFTWARE','source_product':source_product(operation_context)}
        if any(filters.get(k) and filters[k]!=v for k,v in values.items()):continue
        if (filters.get('start_month') or filters.get('end_month')) and (month=='未知' or not service._month(filters.get('start_month') or '1')<=int(month)<=service._month(filters.get('end_month') or '12')):continue
        if metadata_only:
            records.append(values)
            continue
        canonical=normalize_itr(material['business_key']); matches=index[canonical]
        if len(matches)>1 and filters.get('product_code'):
            narrowed=[r for r in matches if r['business_type']==filters['product_code']]
            if narrowed:matches=narrowed
        issue=matches[0] if len(matches)==1 else None
        linked=cs[canonical]; source=linked[0] if len(linked)==1 else None
        facts=json.loads(source['raw_json']) if source else raw
        context=context_from(facts)
        analyses={}
        if issue:
            for stage in ('occurrence','escape','recurrence','capability_gap'):
                latest_analysis=service.issues.get_latest_analysis(issue['knowledge_id'],stage) or {}
                if latest_analysis.get('result'):analyses[stage]=latest_analysis['result']
        root=service._value(analyses.get('occurrence',{}),'root_cause_summary','root_cause')
        escape=service._value(analyses.get('escape',{}),'escape_cause_summary','escape_reason')
        label='LEAKAGE' if root and escape else 'PARTIAL' if analyses and source else 'CS_ONLY' if source and not analyses else 'INSUFFICIENT'
        description=first(facts,'问题信息_问题描述','问题信息_问题主题','问题描述') or first(raw,'问题信息_问题描述','问题描述')
        provenance={'description':'ITR_CS' if source else 'SOFTWARE_OPERATION',
                    'root_cause':'LEAKAGE' if root else 'ITR_CS' if source and (context['root_cause'] or context['trc_root_cause']) else 'MISSING',
                    'escape_reason':'LEAKAGE' if escape else 'MISSING',
                    'trc_correction':'ITR_CS' if source and context['trc_correction'] else 'MISSING'}
        warnings=[]
        if len(matches)>1:warnings.append('关联到多个漏测问题，未自动选择，请核对')
        if len(linked)>1:warnings.append('关联到多个彻底解决单数据组，未自动选择，请核对')
        if not source:warnings.append('未唯一关联彻底解决单，事实暂取软件考核记录')
        if not escape:warnings.append('缺少漏测流出原因，不得推断已完成测试分析')
        field_evidence={key:{'source':'ITR_CS' if source else 'SOFTWARE_OPERATION','value':value,'material_id':source['material_id'] if source else material['material_id']} for key,value in context.items() if value}
        records.append({'knowledge_id':material['material_id'],'business_issue_id':material['business_key'],
            'title':description or '问题描述待补充','description':description,'month':month,'year':year,
            **values,'severity':first(facts,'问题信息_问题等级','问题等级'),
            'source_status':label,'source_label':LABELS[label], 'source_warnings':warnings,
            'source_material_id':material['material_id'],'cs_material_id':source['material_id'] if source else '',
            'source_workbench':'software-operations','canonical_itr':canonical,
            'linked_knowledge_id':issue['knowledge_id'] if issue else '',
            'field_sources':provenance,'field_evidence':field_evidence,'leakage_analysis':analyses,
            'occurrence':{'root_cause':root or ((context['root_cause'] or context['trc_root_cause']) if source else '')},
            'escape':{'reason':escape},'itr_cs_context':context if source else {},
            'supplemental_context':context,'missing_leakage':not bool(analyses),
            'source_hashes':sorted([material['source_hash']]+([source['source_hash']] if source else [])),
            'source_material_ids':[material['material_id']]+([source['material_id']] if source else [])})
    return records

def operation_scope_counts(service, filters=None):
    """Transparent counts for reconciling workbench rows with AI selection."""
    filters=filters or {}
    with service.scenarios.connect() as c:
        raw=c.execute("SELECT COUNT(*) FROM source_material m JOIN data_group g ON g.group_id=m.group_id WHERE m.material_type='SOFTWARE_OPERATION' AND g.group_code='SW-OPS'").fetchone()[0]
        distinct=c.execute("SELECT COUNT(DISTINCT m.group_id||'|'||COALESCE(NULLIF(m.canonical_itr,''),m.business_key)) FROM source_material m JOIN data_group g ON g.group_id=m.group_id WHERE m.material_type='SOFTWARE_OPERATION' AND g.group_code='SW-OPS'").fetchone()[0]
    matched=len(operation_records(service,filters,metadata_only=True))
    return {'raw_count':raw,'distinct_issue_count':distinct,'matched_count':matched,'version_count':max(0,raw-distinct)}
