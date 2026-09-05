"""Scenario consumption and evidence-level insight, additive to existing scenarios."""
import json
import re
import uuid
from collections import defaultdict

CONTEXT_FIELDS = {
    'software_usage_scenario':'软件使用场景（允许暂缺）', 'scenario_description':'质量场景描述',
    'input_resources':'输入资源', 'environment':'环境/工况', 'extreme_conditions':'极限工况',
    'business_objective':'研发业务目标', 'observable_result':'输出物/可观察结果',
    'constraints':'关键约束', 'pain_points':'当前痛点', 'scale_value':'规模数值',
    'scale_unit':'规模单位（轴/PLC/点位/客户端等）',
}
METRIC_FIELDS = {'name':'指标名称','concern':'客户质量关注点','observable':'可观察质量表现',
                 'method':'度量/计算方法','data_source':'数据来源','statistical_scope':'统计口径',
                 'target':'目标值（未确定可空）','evaluation':'评价方法','test_spec':'测试规格/验收建议'}


class ScenarioAssets:
    def __init__(self, repository):
        self.repo = repository
        with self.repo.connect() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS scenario_asset_context(scenario_id TEXT PRIMARY KEY,data_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS scenario_asset_member(member_id TEXT PRIMARY KEY,asset_id TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS scenario_asset_metric(metric_id TEXT PRIMARY KEY,scenario_id TEXT NOT NULL,data_json TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'PROPOSED');
            ''')

    def save_context(self, sid, data):
        if not self.repo.scenario(sid):raise ValueError('场景不存在')
        clean={k:str(data.get(k) or '').strip() for k in CONTEXT_FIELDS}
        if clean['scale_value'] and not re.fullmatch(r'\d+(?:\.\d+)?',clean['scale_value']):raise ValueError('规模数值请填写非负数字，原始规模描述仍保留在场景中')
        with self.repo.connect() as c:c.execute('INSERT OR REPLACE INTO scenario_asset_context VALUES(?,?)',(sid,json.dumps(clean,ensure_ascii=False)))

    def add_metric(self,sid,data):
        if not self.repo.scenario(sid):raise ValueError('场景不存在')
        clean={k:str(data.get(k) or '').strip() for k in METRIC_FIELDS}
        if any(not clean[k] for k in ('name','concern','observable','method','data_source','statistical_scope')):raise ValueError('请完整填写指标名称、关注点、可观察表现、度量方式、数据来源和统计口径')
        with self.repo.connect() as c:c.execute('INSERT INTO scenario_asset_metric VALUES(?,?,?,?)',(uuid.uuid4().hex,sid,json.dumps(clean,ensure_ascii=False),'PROPOSED'))

    def group(self,asset_id,member_ids):
        """Human reviewed grouping retains source candidates and all their evidence."""
        ids=set(member_ids)-{asset_id}
        with self.repo.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            target=c.execute('SELECT product_code FROM quality_scenario WHERE scenario_id=?',(asset_id,)).fetchone()
            if not target:raise ValueError('目标场景不存在')
            if c.execute('SELECT 1 FROM scenario_asset_member WHERE member_id=?',(asset_id,)).fetchone():raise ValueError('目标场景已归入其他资产')
            for sid in ids:
                row=c.execute('SELECT product_code FROM quality_scenario WHERE scenario_id=?',(sid,)).fetchone()
                if not row or row[0]!=target[0]:raise ValueError('归集场景必须存在且属于同一产品；跨产品复用需单独评审')
                if c.execute('SELECT 1 FROM scenario_asset_member WHERE member_id=? OR asset_id=?',(sid,sid)).fetchone():raise ValueError('候选已被归集或含下属场景，请先核对')
            for sid in ids:c.execute('INSERT INTO scenario_asset_member(member_id,asset_id) VALUES(?,?)',(sid,asset_id))

    def ungroup(self,asset_id,member_ids):
        with self.repo.connect() as c:
            for sid in set(member_ids):c.execute('DELETE FROM scenario_asset_member WHERE member_id=? AND asset_id=?',(sid,asset_id))

    def catalog(self):
        scenarios=self.repo.scenarios()
        with self.repo.connect() as c:
            groups={r['member_id']:r['asset_id'] for r in c.execute('SELECT * FROM scenario_asset_member')}
            contexts={r['scenario_id']:json.loads(r['data_json']) for r in c.execute('SELECT * FROM scenario_asset_context')}
            evidence=defaultdict(set)
            for r in c.execute('SELECT scenario_id,knowledge_id FROM quality_scenario_evidence'):evidence[r[0]].add(r[1])
            metrics=defaultdict(list)
            for r in c.execute('SELECT * FROM scenario_asset_metric'):metrics[r['scenario_id']].append({**json.loads(r['data_json']),'metric_id':r['metric_id'],'status':r['status']})
        by_id={s['scenario_id']:s for s in scenarios}
        assets=[]
        for s in scenarios:
            sid=s['scenario_id']
            if sid in groups:continue
            members=[sid]+[m for m,a in groups.items() if a==sid and m in by_id]
            assets.append({**s,'context':contexts.get(sid,{}),'members':[{**by_id[m],'context':contexts.get(m,{})} for m in members],
                           'issue_ids':sorted(set().union(*(evidence[m] for m in members))),
                           'metrics':[metric for m in members for metric in metrics[m]]})
        return assets

    def facts(self):
        """Use current issue period and per-issue CS facts, never spread aggregate scopes."""
        with self.repo.connect() as c:
            tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            facts={}
            if {'quality_issue','quality_issue_version'}<=tables:
                facts={r['knowledge_id']:dict(r) for r in c.execute('SELECT q.knowledge_id,q.business_issue_id,q.business_type,v.* FROM quality_issue q JOIN quality_issue_version v ON v.issue_version_id=q.current_version_id')}
            if {'issue_material_link','source_material'}<=tables:
                for r in c.execute("SELECT l.knowledge_id,m.raw_json FROM issue_material_link l JOIN source_material m ON m.material_id=l.material_id WHERE m.material_type='ITR_CS' ORDER BY m.version_no"):
                    raw=json.loads(r['raw_json'] or '{}');f=facts.setdefault(r['knowledge_id'],{})
                    for key,names in {'industry':['问题信息_客户行业','客户行业'],'customer':['问题信息_客户名称','客户名称'],'product':['问题信息_产品型号','产品型号']}.items():
                        f[key]=next((str(raw[n]).strip() for n in names if raw.get(n)), '')
            if 'scenario_generation_source' in tables:
                for snapshot in c.execute('SELECT records_json FROM scenario_generation_source ORDER BY rowid'):
                    for row in json.loads(snapshot[0]):
                        context=row.get('itr_cs_context') or {}
                        facts[row['knowledge_id']]={'business_issue_id':row.get('business_issue_id'),'title':row.get('description'),
                            'industry':context.get('customer_industry'),'customer':context.get('customer_name'),
                            'product':context.get('product_model'),'year':row.get('year'),'month':row.get('month'),
                            'period_status':'时间缺失' if row.get('year') in ('',None,'未知') or row.get('month') in ('',None,'未知') else '年月完整',
                            'period_source':'KPI计入月份' if row.get('source_workbench')=='software-operations' else '彻底解决单/ITR事实时间',
                            'source_workbench':row.get('source_workbench') or '',
                            'source_material_id':row.get('source_material_id') or '',
                            'cs_material_id':row.get('cs_material_id') or '',
                            'itr_material_id':row.get('itr_material_id') or '',
                            'source_status':row.get('source_status') or '',
                            'field_evidence':row.get('field_evidence') or {}}
            from quality_knowledge.scenario_evidence import enrich_facts
            facts=enrich_facts(c,facts)
        return facts

    def report(self,filters=None):
        filters=filters or {};facts=self.facts();records=[];assets=self.catalog()
        semantic_labels={}
        for code in {a.get('product_code') or '' for a in assets}|{''}:
            semantic_labels.update({x['term_code']:x['label_zh'] for x in self.repo.semantic_dictionary(code,False)['items']})
        if filters.get('grain','quarter') not in ('quarter','half','year'):
            raise ValueError('不支持的时间粒度')
        for asset in assets:
            if filters.get('status') and asset['status']!=filters['status']:
                continue
            for member in asset['members']:
                with self.repo.connect() as c:ids=[r[0] for r in c.execute('SELECT knowledge_id FROM quality_scenario_evidence WHERE scenario_id=?',(member['scenario_id'],))]
                for kid in ids:
                    f=facts.get(kid,{})
                    raw_id=str(f.get('business_issue_id') or kid).strip().upper()
                    issue_key=re.sub(r'CS$','',raw_id) if raw_id.startswith('ITR') else kid
                    year=str(f.get('year') or '')
                    month=re.search(r'\d+',str(f.get('month') or ''))
                    m=int(month[0]) if month else 0
                    period='未知时间'
                    if re.fullmatch(r'\d{4}',year) and 1<=m<=12:
                        grain=filters.get('grain','quarter')
                        period=year if grain=='year' else f'{year} H{(m-1)//6+1}' if grain=='half' else f'{year} Q{(m-1)//3+1}'
                    context=member['context']
                    scale=(context.get('scale_value','')+' '+context.get('scale_unit','')).strip() or member.get('system_scale') or '未知规模'
                    formal_environment='；'.join(str(member.get(k) or '').strip() for k in ('operating_environment','operating_condition','duration_frequency','disturbances','extreme_conditions') if str(member.get(k) or '').strip() and str(member.get(k) or '').strip()!='未知')
                    concern=semantic_labels.get(member.get('primary_quality_concern_code')) or member.get('concern_points') or '未知关注点'
                    row={'asset_id':asset['scenario_id'],'scenario':asset['scenario_id'],'issue_id':kid,'issue_key':issue_key,
                         'industry':str(f.get('industry') or '未知行业'),'customer':str(f.get('customer') or '未知客户'),
                         'product':str(f.get('product') or '未知产品型号'),'business':asset.get('product_code') or '未知业务',
                         'lifecycle':member.get('lifecycle_code') or '未知阶段','activity':member.get('activity_code') or '未知活动',
                         'scale':scale,'environment':context.get('environment') or formal_environment or '；'.join(str(member.get(k) or '') for k in ('preconditions','trigger_conditions') if member.get(k)) or '未知工况',
                         'environment_source':'人工工况' if context.get('environment') else '场景结构化工况' if formal_environment else '前置/触发条件原文' if member.get('preconditions') or member.get('trigger_conditions') else '缺失',
                         'period_status':f.get('period_status','时间未完整提供'),'kpi_raw':f.get('kpi_raw',''),
                         'period_source':f.get('period_source',''),'source_workbench':f.get('source_workbench',''),
                         'concern':concern,'quality':member.get('quality_attribute') or '未知属性','period':period}
                    if all(not filters.get(k) or row.get(k)==filters[k] for k in ('industry','customer','product','business','activity','lifecycle','scale','environment','concern','quality','period','asset_id')):records.append(row)
        visible={r['asset_id'] for r in records}
        active_filters=any(filters.get(k) for k in ('industry','customer','product','business','activity','lifecycle','scale','environment','concern','quality','period','asset_id'))
        selected=[{**a,'matching_issue_count':len({r['issue_key'] for r in records if r['asset_id']==a['scenario_id']})}
                  for a in assets if (not filters.get('status') or a['status']==filters['status']) and (not active_filters or a['scenario_id'] in visible)]
        def counts(key):
            buckets=defaultdict(set)
            for r in records:buckets[r[key]].add(r['issue_key'])
            rows=[{'label':k,'count':len(v)} for k,v in buckets.items()]
            return sorted(rows,key=lambda r:r['label'] if key=='period' else (-r['count'],r['label']))
        x=filters.get('x','industry');y=filters.get('y','scenario')
        dimensions=('industry','customer','product','business','lifecycle','activity','scale','environment','concern','quality','period','scenario')
        if x not in dimensions or y not in dimensions:raise ValueError('不支持的交叉维度')
        cells=defaultdict(set)
        for r in records:cells[(r[x],r[y])].add(r['issue_key'])
        matched={r['issue_key']:{**r,**facts.get(r['issue_id'],{})} for r in records}
        return {'assets':selected,'records':records,'issue_count':len({r['issue_key'] for r in records}),
                'matched_issues':list(matched.values()),'period_readiness':counts('period_status'),
                'customer_count':len({r['customer'] for r in records if r['customer']!='未知客户'}),
                'industry_count':len({r['industry'] for r in records if r['industry']!='未知行业'}),
                'metric_count':sum(len(a['metrics']) for a in selected),'distributions':{k:counts(k) for k in dimensions},
                'cells':[{'x':a,'y':b,'count':len(v)} for (a,b),v in sorted(cells.items())],
                'unknown_time_count':len({r['issue_key'] for r in records if r['period']=='未知时间'})}

    def portrait(self, filters=None):
        """A factual customer/industry portrait; it never invents topology."""
        filters=filters or {}
        report=self.report(filters)
        records=report['records']
        def grouped(key):
            buckets=defaultdict(lambda:{'issues':set(),'activities':set(),'lifecycles':set(),'conditions':set(),'scenarios':set()})
            for row in records:
                bucket=buckets[row[key]]
                bucket['issues'].add(row['issue_key']);bucket['activities'].add(row['activity'])
                bucket['lifecycles'].add(row['lifecycle']);bucket['conditions'].add(row['environment'])
                bucket['scenarios'].add(row['asset_id'])
            result=[]
            for label,value in buckets.items():
                result.append({'label':label,'issue_count':len(value['issues']),'scenario_count':len(value['scenarios']),
                               'activities':sorted(value['activities']),'lifecycles':sorted(value['lifecycles']),
                               'conditions':sorted(value['conditions'])})
            return sorted(result,key=lambda x:(-x['issue_count'],x['label']))
        source_counts=defaultdict(set)
        for row in records:
            source_counts[row.get('source_workbench') or 'unknown'].add(row['issue_key'])
        report['product_portrait']=grouped('product')
        report['activity_portrait']=grouped('activity')
        report['customer_portrait']=grouped('customer')
        report['source_counts']={key:len(value) for key,value in source_counts.items()}
        return report
