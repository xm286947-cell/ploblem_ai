"""Read current KPI period and original conditions without rewriting scenarios."""
import json
from collections import defaultdict
from quality_knowledge.materials import normalize_itr
from quality_knowledge.scenario_sources import period, first

def enrich_facts(connection, facts):
    tables={r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'source_material' not in tables:return facts
    materials=[dict(r) for r in connection.execute('SELECT m.*,y.reporting_year FROM source_material m LEFT JOIN source_material_reporting_year y ON y.material_id=m.material_id ORDER BY m.version_no DESC,m.created_at DESC,m.material_id')]
    latest={};by_id={m['material_id']:m for m in materials}
    for m in materials:latest.setdefault((m['group_id'],normalize_itr(m['business_key'])),m)
    ops=defaultdict(list);cs=defaultdict(list);issues=defaultdict(list)
    operation_groups={r[0] for r in connection.execute("SELECT group_id FROM data_group WHERE group_code='SW-OPS'")}
    for m in latest.values():
        if m['material_type']=='SOFTWARE_OPERATION' and m['group_id'] in operation_groups:ops[normalize_itr(m['business_key'])].append(m)
        if m['material_type']=='ITR_CS':cs[normalize_itr(m['business_key'])].append(m)
    if 'quality_issue' in tables:
        for q in connection.execute('SELECT knowledge_id,business_issue_id,business_type FROM quality_issue'):
            facts.setdefault(q['knowledge_id'],{}).update({'business_issue_id':q['business_issue_id'],'business_type':q['business_type']})
            issues[normalize_itr(q['business_issue_id'])].append(q['knowledge_id'])
    for e in connection.execute('SELECT DISTINCT knowledge_id FROM quality_scenario_evidence'):
        kid=e[0]
        if kid in by_id:facts.setdefault(kid,{})['business_issue_id']=by_id[kid]['business_key']
    for kid,f in facts.items():
        canonical=normalize_itr(f.get('business_issue_id') or '')
        candidates=ops[canonical]
        f.update(year='',month='',period_status='未关联考核记录',period_source='KPI计入月份')
        if len(candidates)==1:
            m=candidates[0];raw=json.loads(m['raw_json'])
            if m.get('reporting_year'):raw['数据运营_KPI计入年份']=m['reporting_year']
            year,month=period(raw)
            f.update(year=year,month=month,kpi_material_id=m['material_id'],kpi_raw=first(raw,'数据运营_KPI计入月份','KPI计入月份'),
                     period_status='月份缺失' if month=='未知' else '月份已知、年份缺失' if year=='未知' else '年月完整')
        elif len(candidates)>1:f['period_status']='考核关联冲突'
        linked=cs[canonical]
        if len(linked)==1:
            raw=json.loads(linked[0]['raw_json']);f['cs_material_id']=linked[0]['material_id']
            f['cs_context']={key:raw.get(key) for key in ('问题信息_问题描述','问题信息_问题原因定位','问题信息_问题发生阶段','问题信息_当前客户状态','技术根因分析与纠正_TRC纠正信息','技术根因分析与纠正_TRC根因','问题处理结果_问题解决方案') if raw.get(key)}
            for key,names in {'industry':('问题信息_客户行业','客户行业'),'customer':('问题信息_客户名称','客户名称'),'product':('问题信息_产品型号','产品型号'),'description':('问题信息_问题描述','问题描述')}.items():
                value=first(raw,*names)
                if value:f[key]=value
        f['analysis_id']=kid if not kid.startswith('MAT-') else issues[canonical][0] if len(issues[canonical])==1 else ''
    return facts
