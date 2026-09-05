"""Software-operation selection, with explicit field-level evidence provenance."""
import json
import re
from collections import defaultdict
from quality_knowledge.materials import normalize_itr

LABELS = {'LEAKAGE':'有漏测分析', 'PARTIAL':'漏测分析不完整，已补充彻底解决单',
          'CS_ONLY':'无漏测分析，基于彻底解决单', 'INSUFFICIENT':'依据不足，待补充'}

def first(raw, *keys):
    return next((str(raw[k]).strip() for k in keys if raw.get(k) not in (None, '', [], {})), '')

def period(raw):
    value=first(raw,'数据运营_KPI计入月份','KPI计入月份')
    year=first(raw,'数据运营_KPI计入年份','数据运营_KPI计入年度','KPI计入年份','考核年份')
    dated=re.search(r'(20\d{2})\s*(?:年|[-/.])\s*(\d{1,2})',value)
    if dated:return year or dated[1],str(int(dated[2]))
    month=re.fullmatch(r'\s*(\d{1,2})\s*月?\s*',value)
    return year or '未知', str(int(month[1])) if month and 1<=int(month[1])<=12 else '未知'

def operation_records(service, filters=None, selected_ids=None, metadata_only=False):
    filters=filters or {}; selected=set(selected_ids or [])
    with service.scenarios.connect() as c:
        materials=[dict(r) for r in c.execute("SELECT m.*,y.reporting_year FROM source_material m JOIN data_group g ON g.group_id=m.group_id LEFT JOIN source_material_reporting_year y ON y.material_id=m.material_id WHERE m.material_type='ITR_CS' OR (m.material_type='SOFTWARE_OPERATION' AND g.group_code='SW-OPS') ORDER BY m.version_no DESC,m.created_at DESC,m.material_id")]
        issues=[dict(r) for r in c.execute('SELECT knowledge_id,business_issue_id,business_type FROM quality_issue')]
    latest={}; cs=defaultdict(list); index=defaultdict(list)
    for row in materials:latest.setdefault((row['material_type'],row['group_id'],normalize_itr(row['business_key'])),row)
    for row in latest.values():
        if row['material_type']=='ITR_CS':cs[normalize_itr(row['business_key'])].append(row)
    for row in issues:index[normalize_itr(row['business_issue_id'])].append(row)
    records=[]
    for material in latest.values():
        if material['material_type']!='SOFTWARE_OPERATION' or (selected and material['material_id'] not in selected):continue
        raw=json.loads(material['raw_json'])
        if material.get('reporting_year'):raw['数据运营_KPI计入年份']=material['reporting_year']
        year,month=period(raw)
        values={'ipmt':first(raw,'问题信息_IPMT','IPMT'),'spdt':first(raw,'问题信息_SPDT','SPDT'),
                'product_model':first(raw,'问题信息_产品型号','产品型号'),'year':year,'month':month}
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
        context={key:first(facts,*names) for key,names in {
            'ipmt':('问题信息_IPMT','IPMT'),'spdt':('问题信息_SPDT','SPDT'),
            'product_model':('问题信息_产品型号','产品型号'),'customer_industry':('问题信息_客户行业','客户行业'),
            'customer_name':('问题信息_客户名称','客户名称'),'customer_level':('问题信息_客户分级','问题信息_客户吸引力','客户分级'),
            'customer_status':('问题信息_当前客户状态','问题信息_当前问题状态'),
            'occurrence_phase':('问题信息_问题发生阶段','问题发生阶段'),
            'root_cause':('问题信息_问题原因定位','问题原因定位'),
            'trc_correction':('技术根因分析与纠正_TRC纠正信息','TRC纠正信息'),
            'trc_root_cause':('技术根因分析与纠正_TRC根因','技术根因分析与纠正_根因分析','TRC根因'),
            'solution':('问题处理结果_问题解决方案','技术根因分析与纠正_解决方案','解决方案')}.items()}
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
        records.append({'knowledge_id':material['material_id'],'business_issue_id':material['business_key'],
            'title':description or '问题描述待补充','description':description,'month':month,'year':year,
            **values,'severity':first(facts,'问题信息_问题等级','问题等级'),
            'source_status':label,'source_label':LABELS[label], 'source_warnings':warnings,
            'source_material_id':material['material_id'],'cs_material_id':source['material_id'] if source else '',
            'linked_knowledge_id':issue['knowledge_id'] if issue else '',
            'field_sources':provenance,'leakage_analysis':analyses,
            'occurrence':{'root_cause':root or ((context['root_cause'] or context['trc_root_cause']) if source else '')},
            'escape':{'reason':escape},'itr_cs_context':context if source else {},
            'supplemental_context':context,'missing_leakage':not bool(analyses)})
    return records
