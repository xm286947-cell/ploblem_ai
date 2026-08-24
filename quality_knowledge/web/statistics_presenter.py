from __future__ import annotations
from urllib.parse import urlencode

SECTION_TITLES = {
    'top_occurrence_causes': 'TOP 发生原因', 'top_escape_causes': 'TOP 流出原因',
    'product_distribution': '产品分布', 'top_technical_gaps': '技术能力缺口 TOP5',
    'top_management_gaps': '管理能力缺口 TOP5', 'top_governance_gaps': '治理能力缺口 TOP5',
    'cross_product_common_gaps': '跨产品共性能力缺口', 'common_capability_analysis': '跨产品共性能力分析',
}
FIELD_LABELS = {
    'category': '能力缺口/分类', 'count': '问题数', 'business_count': '涉及业务数', 'businesses': '涉及业务',
    'dimension': '能力维度', 'gap_dimension': '能力维度', 'gap_category': '能力分类',
    'related_issue_count': '关联问题数', 'affected_products': '涉及产品', 'products': '涉及产品',
    'platforms': '涉及平台', 'recommended_governance': '建议公共措施', 'recommended_action': '建议措施',
    'common_evidence': '共性证据', 'description': '说明', 'business_type': '业务类型', 'product': '产品',
    'platform': '平台', 'module': '模块/特性',
}
VALUE_LABELS = {
    'TECHNICAL': '技术能力', 'MANAGEMENT': '管理能力', 'GOVERNANCE': '治理能力',
    'HIGH': '高', 'MEDIUM': '中', 'LOW': '低', 'UNKNOWN': '未知',
    'PROCESS': '流程机制', 'REVIEW': '评审', 'CHANGE_MANAGEMENT': '变更管理', 'MANDATORY_TEST': '必测机制',
    'ENTRY_EXIT_CRITERIA': '准入/准出标准', 'QUALITY_GATE': '质量门禁', 'ISSUE_CLOSURE': '问题闭环',
    'TRAINING': '培训', 'ROLE_RESPONSIBILITY': '角色职责', 'KNOWLEDGE_REUSE': '知识复用',
    'CROSS_TEAM_COLLABORATION': '跨团队协同', 'TECH_METHOD': '技术方法', 'DESIGN_METHOD': '设计方法',
    'TEST_METHOD': '测试方法', 'TEST_CAPABILITY': '测试能力', 'AUTOMATION': '自动化', 'OBSERVABILITY': '可观测性',
    'METRIC': '度量指标', 'TECH_STANDARD': '技术标准', 'TEMPLATE_GUIDE': '模板/指南', 'TOOL': '工具',
    'TEST_ENVIRONMENT': '测试环境', 'TEST_DATA': '测试数据', 'STATIC_ANALYSIS': '静态分析',
    'DESIGN_GUARDRAIL': '设计防护', 'HORIZONTAL_REPLICATION': '横向复制',
    'CROSS_PRODUCT_GOVERNANCE': '跨产品治理', 'COMMON_STANDARD': '公共标准',
    'COMMON_PLATFORM_CAPABILITY': '公共平台能力', 'COMMON_TEST_ASSET': '公共测试资产',
    'COMMON_CASE_LIBRARY': '公共案例库', 'COMMON_METRIC': '公共度量', 'ORGANIZATION_MECHANISM': '组织机制',
}
ORDER = ['top_occurrence_causes','top_escape_causes','product_distribution','top_technical_gaps','top_management_gaps','top_governance_gaps','cross_product_common_gaps','common_capability_analysis']

def zh_value(value):
    if value is None or value == '': return '暂无'
    if isinstance(value, bool): return '是' if value else '否'
    if isinstance(value, (list, tuple)): return '、'.join(zh_value(x) for x in value)
    return VALUE_LABELS.get(str(value), str(value))

def _pct(rows):
    total=sum(int(x.get('count') or 0) for x in rows if isinstance(x,dict)) or 1
    return [{**x,'percent':round(int(x.get('count') or 0)*100/total,1)} for x in rows if isinstance(x,dict)]

def present_statistics(stats: dict, runtime: dict | None = None) -> dict:
    sections=[]
    for key in ORDER:
        rows=stats.get(key) or []
        if not isinstance(rows,list): continue
        display=[]; columns=[]
        for row in rows:
            if not isinstance(row,dict): continue
            if not columns: columns=list(row.keys())
            display.append({k:zh_value(v) for k,v in row.items()})
        sections.append({'key':key,'title':SECTION_TITLES.get(key,key),'columns':[{'key':k,'label':FIELD_LABELS.get(k,k)} for k in columns],'rows':display})
    products=stats.get('product_distribution') or []
    total=sum(int(x.get('count') or 0) for x in products if isinstance(x,dict))
    common=stats.get('common_capability_analysis') or stats.get('cross_product_common_gaps') or []
    runtime=runtime or {}
    risk=_pct(runtime.get('risk_distribution') or [])
    return {'sections':sections,'summary':{
        'issue_count':runtime.get('issue_count',total),'product_count':len(products),'common_gap_count':len(common),
        'technical_gap_types':len(stats.get('top_technical_gaps') or []),
        'analyzed_count':runtime.get('analyzed_count',0),'human_analyzed_count':runtime.get('human_analyzed_count',0),
        'ai_completion_rate':round(runtime.get('analyzed_count',0)*100/(runtime.get('issue_count') or total or 1),1),
        'human_completion_rate':round(runtime.get('human_analyzed_count',0)*100/(runtime.get('issue_count') or total or 1),1),
        'high_risk_count':runtime.get('high_risk_count',0),
        'management_gap_types':len(stats.get('top_management_gaps') or []),'governance_gap_types':len(stats.get('top_governance_gaps') or []),
    },'product_chart':_pct(products),'risk_chart':risk}


def _csv_values(value):
    if value is None: return []
    if isinstance(value,(list,tuple)): return [str(x).strip() for x in value if str(x).strip()]
    return [x.strip() for x in str(value).split(',') if x.strip()]

def present_common_gaps(rows, *, business_type='', dimension='', scope='all'):
    """UED R4 V2 presentation model. No Knowledge/AI semantic changes."""
    items=[]
    for raw in rows or []:
        if not isinstance(raw,dict):
            continue
        dim=str(raw.get('dimension') or '').upper()
        bt_count=int(raw.get('business_type_count') or 0)
        businesses=_csv_values(raw.get('business_types'))
        products=[]
        for x in _csv_values(raw.get('products')):
            if x and x not in products:
                products.append(x)
        platforms=[]
        for x in _csv_values(raw.get('platforms')):
            if x and x not in platforms:
                platforms.append(x)
        related_ids=_csv_values(raw.get('related_issues'))
        related_knowledge_ids=_csv_values(raw.get('related_knowledge_ids'))
        count=int(raw.get('related_issue_count') or len(related_ids))
        category_key=str(raw.get('category') or '')
        item={
            'category': zh_value(category_key),
            'category_key': category_key,
            'dimension': dim,
            'dimension_label': zh_value(dim),
            'related_issue_count': count,
            'related_issue_ids': related_ids,
            'related_knowledge_ids': related_knowledge_ids,
            'coverage_count': bt_count,
            'businesses': businesses,
            'products': products,
            'platforms': platforms,
            'is_cross_product': bt_count >= 2,
            'scope_label': '跨产品共性' if bt_count >= 2 else '产品内共性',
            'recommended_governance': raw.get('recommended_governance') or '',
            'expected_prevention_effect': raw.get('expected_prevention_effect') or '',
        }
        if dimension and dim != dimension.upper():
            continue
        if scope == 'cross' and not item['is_cross_product']:
            continue
        items.append(item)

    # UED priority: related issues first, then coverage breadth.
    items.sort(key=lambda x:(x['related_issue_count'],x['coverage_count']),reverse=True)
    max_count=max([x['related_issue_count'] for x in items] or [1])
    for i,x in enumerate(items,1):
        x['rank']=i
        x['bar_percent']=round(x['related_issue_count']*100/max_count)
        # Semantic drill-down stays correct even when a group contains more IDs
        # than a URL should carry, and it always resolves against current versions.
        params=[('gap_dimension', x['dimension']), ('gap_category', x['category_key'])]
        if x['related_knowledge_ids'] and len(x['related_knowledge_ids']) <= 50:
            params.append(('knowledge_ids', ','.join(x['related_knowledge_ids'])))
        elif not x['related_knowledge_ids'] and x['related_issue_ids']:
            params.append(('business_issue_ids', ','.join(x['related_issue_ids'])))
        x['drilldown_url']='/issues?'+urlencode(params)

    dim_counts={}
    for x in items:
        dim_counts[x['dimension_label']]=dim_counts.get(x['dimension_label'],0)+1
    top_dim=max(dim_counts,key=dim_counts.get) if dim_counts else '暂无'
    top_dim_pct=round(dim_counts.get(top_dim,0)*100/len(items)) if items else 0

    # Knowledge IDs are globally unique; business issue numbers may be reused by
    # different products and must not be used as the de-duplication key.
    selected_knowledge_ids=set()
    for x in items:
        selected_knowledge_ids.update(x['related_knowledge_ids'])
    related_unique=len(selected_knowledge_ids) if selected_knowledge_ids else sum(x['related_issue_count'] for x in items)

    cross_items=[x for x in items if x['is_cross_product']]
    return {
        'items':items,
        'cross_items':cross_items,
        'summary':{
            'common_gap_count':len(items),
            'related_issue_count':related_unique,
            'cross_product_count':len(cross_items),
            'top_dimension':top_dim,
            'top_dimension_percent':top_dim_pct,
        },
        'business_type':business_type,
        'dimension':dimension,
        'scope':scope,
    }
