"""Evidence-backed screening themes; suggestions are not diagnosed root causes."""
import re
from collections import defaultdict

THEMES = (
    ('trust','状态与结果不可信',r'状态.*(错误|异常|不一致)|显示.*(错误|异常|未刷新)|反馈.*(错误|不一致)|不可信|定位.*错误',
     '核对界面、工程模型与设备实际状态的一致性要求',
     '补充编辑、下载、重连及多设备条件下的状态转换与反馈校验',
     '状态一致性、反馈延迟；明确采样点、参考真值与观测窗口'),
    ('blocked','操作受阻或任务无法完成',r'无法|不能|失败|失效|受限|阻塞|缺失',
     '明确业务任务的前置条件、失败反馈与恢复路径',
     '验证业务链路正常路径及依赖缺失、输入异常、失败恢复分支',
     '任务完成率、恢复耗时；明确任务集、成功判据及失败归类'),
    ('slow','响应迟缓或交互不流畅',r'卡顿|响应慢|延迟|越来越慢|刷新慢|掉帧',
     '明确规模、负载与持续运行条件下的响应目标',
     '固定规模和负载，验证响应分布及持续运行中的退化',
     '响应时间P95、超时次数；明确起止事件、负载与采样时长'),
    ('loss','数据丢失或恢复不正确',r'数据.*丢失|变量.*丢失|清零|恢复.*(错误|异常)|损坏',
     '明确数据保存、持久化与恢复的一致性边界',
     '验证正常保存、异常中断、重启恢复及重复循环后的数据校验',
     '数据恢复正确率、恢复耗时；明确应保留数据集及校验方法'),
    ('confusing','信息不清或操作易误解',r'提示.*(不清|错误)|文档.*不一致|帮助.*不一致|难以理解|误操作',
     '核对操作语义、提示信息和帮助说明是否一致',
     '按真实用户任务验证提示可理解性、错误预防和恢复指引',
     '任务误操作次数、完成时间；明确用户类型、任务与观测步骤'),
)

def decision_digest(report, repository):
    rows=report['records']; buckets={}; matched=set(); labels={}; concerns={}
    for business in {r['business'] for r in rows}:
        taxonomy=repository.taxonomy(product_code=business)
        if taxonomy:
            labels.update({(business,a['activity_code']):a['label_zh'] for a in taxonomy['activities']})
    for row in rows:
        for code,label,pattern,design,test,metric in THEMES:
            hit=re.search(pattern,row['concern'])
            if not hit:continue
            if re.search(r'(无|没有|未出现|不存在|避免|防止)$',row['concern'][max(0,hit.start()-5):hit.start()]):
                continue
            concerns.setdefault(label,set()).add(row['issue_key'])
            matched.add(row['issue_key'])
            key=(row['business'],row['activity'],code)
            entry=buckets.setdefault(key,{'theme':label,'business':row['business'],'activity':labels.get(key[:2],row['activity']),
                'design':design,'test':test,'metric':metric,'issues':{},'industries':set(),'customers':set(),'scales':set(),'conditions':set(),'assets':set()})
            entry['issues'].setdefault(row['issue_key'],{**row,'matched_text':hit[0]})
            entry['industries'].add(row['industry']);entry['customers'].add(row['customer'])
            entry['scales'].add(row['scale']);entry['conditions'].add(row['environment']);entry['assets'].add(row['asset_id'])
    themes=[]
    for entry in buckets.values():
        entry['count']=len(entry['issues']);entry['issues']=list(entry['issues'].values())
        for field in ('industries','customers','scales','conditions','assets'):entry[field]=sorted(entry[field])
        themes.append(entry)
    themes.sort(key=lambda e:(-e['count'],e['business'],e['activity'],e['theme']))
    total=report['issue_count']
    unknown=lambda key,value:len({r['issue_key'] for r in rows if r[key]==value})
    return {'themes':themes[:3],'theme_count':len(themes),'total':total,'unmatched':total-len(matched),
        'concerns':sorted([{'label':label,'count':len(ids)} for label,ids in concerns.items()],key=lambda r:(-r['count'],r['label'])),
        'unknown_time':report['unknown_time_count'],'unknown_industry':unknown('industry','未知行业'),
        'unknown_scale':unknown('scale','未知规模'),'unknown_environment':unknown('environment','未知工况'),
        'unpublished':sum(a['status']!='PUBLISHED' for a in report['assets']),
        'no_metrics':sum(not a['metrics'] for a in report['assets']),
        'asset_count':len(report['assets'])}


def portrait_digest(rows):
    """Summarise the complete CS/ITR portrait scope without inventing causality."""
    rows=list(rows or [])

    def value(row,key):
        raw=(row.get('field_evidence') or {}).get(key)
        return str(raw.get('value') if isinstance(raw,dict) else raw or '').strip()

    def ranked(key, fallback=''):
        buckets=defaultdict(set)
        for row in rows:
            label=str(row.get(key) or fallback).strip() or fallback
            if label:buckets[label].add(row.get('id') or row.get('number'))
        return sorted(({'label':label,'count':len(ids)} for label,ids in buckets.items()),
                      key=lambda item:(-item['count'],item['label']))

    def evidence_count(*keys):
        return sum(any(value(row,key) for key in keys) for row in rows)

    domain_labels={'SOFTWARE':'软件','HARDWARE':'硬件','MECHANICAL':'机械','UNKNOWN':'待识别'}
    domain_rows=ranked('problem_domain','UNKNOWN')
    for item in domain_rows:item['label']=domain_labels.get(item['label'],item['label'])
    products=ranked('product','未知产品')
    product_groups=ranked('product_group','未知产品分类')
    industries=ranked('industry','未知行业')
    customers=ranked('customer','未知客户')
    periods=defaultdict(set)
    for row in rows:
        year=str(row.get('year') or '').strip();month=str(row.get('month') or '').strip()
        label=f'{year}-{int(month):02d}' if year.isdigit() and month.isdigit() and 1<=int(month)<=12 else '未知时间'
        periods[label].add(row.get('id') or row.get('number'))
    period_rows=[{'label':label,'count':len(ids)} for label,ids in sorted(periods.items())]

    scene_rows=[row for row in rows if row.get('scenarios')]
    activities=defaultdict(set);lifecycles=defaultdict(set);qualities=defaultdict(set)
    product_domain=defaultdict(lambda:defaultdict(set));group_detail={};cross_product=[]
    for row in rows:
        issue=row.get('id') or row.get('number');product=str(row.get('product') or '未知产品')
        group=str(row.get('product_group') or '未知产品分类')
        product_domain[product][domain_labels.get(row.get('problem_domain') or 'UNKNOWN','待识别')].add(issue)
        detail=group_detail.setdefault(group,{'label':group,'issues':set(),'models':defaultdict(set),'domains':defaultdict(set),'activities':defaultdict(set),'scene_issues':set()})
        detail['issues'].add(issue);detail['models'][product].add(issue)
        detail['domains'][domain_labels.get(row.get('problem_domain') or 'UNKNOWN','待识别')].add(issue)
        scene_businesses=set()
        for scene in row.get('scenarios') or []:
            detail['scene_issues'].add(issue)
            if scene.get('business'):scene_businesses.add(str(scene['business']))
            for target,key in ((activities,'activity'),(lifecycles,'lifecycle'),(qualities,'quality')):
                label=str(scene.get(key) or '').strip()
                if label:
                    target[label].add(issue)
                    if key=='activity':detail['activities'][label].add(issue)
        if len(scene_businesses)>1:
            cross_product.append({'id':issue,'number':row.get('number') or issue,'description':row.get('description') or '',
                                  'products':sorted(scene_businesses)})
    def set_rows(buckets):
        return sorted(({'label':label,'count':len(ids)} for label,ids in buckets.items()),key=lambda item:(-item['count'],item['label']))
    matrix=[]
    for product in [item['label'] for item in products[:8]]:
        matrix.append({'label':product,'total':sum(len(ids) for ids in product_domain[product].values()),
                       'domains':{key:len(ids) for key,ids in product_domain[product].items()}})
    group_cards=[]
    for group in [item['label'] for item in product_groups]:
        detail=group_detail[group]
        models=set_rows(detail['models'])[:3];group_activities=set_rows(detail['activities'])[:3]
        group_cards.append({'label':group,'count':len(detail['issues']),'models':models,
            'domains':{key:len(ids) for key,ids in detail['domains'].items()},
            'scene_count':len(detail['scene_issues']),'activities':group_activities})

    total=len(rows);scene_count=len(scene_rows);cause_count=evidence_count('root_cause','trc_root_cause')
    phase_count=evidence_count('occurrence_phase');status_count=evidence_count('customer_status')
    top_product=products[0] if products else None;top_domain=domain_rows[0] if domain_rows else None
    conclusions=[]
    if top_product:
        conclusions.append({'title':'问题主要集中范围','level':'focus',
            'text':f"{top_product['label']}涉及 {top_product['count']} 个问题（{top_product['count']/total:.0%}）；问题领域以{top_domain['label']}为主（{top_domain['count']} 个）。",
            'action':'先下钻该产品的业务活动和原始问题，确认是否存在可归一的共性场景。'})
    if total:
        conclusions.append({'title':'场景资产覆盖','level':'warning' if scene_count<total else 'good',
            'text':f'已有质量场景覆盖 {scene_count}/{total} 个问题（{scene_count/total:.0%}），其余 {total-scene_count} 个仍只能按问题事实观察。',
            'action':'优先处理数量高且尚无场景的问题簇，避免画像只反映已建设资产。' if scene_count<total else '继续核查已有场景的边界与指标是否完整。'})
        conclusions.append({'title':'分析证据完备度','level':'warning' if cause_count<total else 'good',
            'text':f'根因信息 {cause_count}/{total}，发生阶段 {phase_count}/{total}，客户状态 {status_count}/{total}。',
            'action':'根因不足时不得形成确定性原因结论；先补齐证据或触发AI逐问题提炼并人工评审。'})
    return {'total':total,'products':products[:10],'product_groups':group_cards,'industries':industries[:10],'customers':customers[:10],
            'domains':domain_rows,'periods':period_rows,'activities':set_rows(activities)[:10],
            'lifecycles':set_rows(lifecycles)[:10],'qualities':set_rows(qualities)[:10],
            'product_domain':matrix,'scene_count':scene_count,'cause_count':cause_count,
            'phase_count':phase_count,'customer_status_count':status_count,'cross_product':cross_product[:10],'conclusions':conclusions}
