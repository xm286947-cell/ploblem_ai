"""Evidence-backed screening themes; suggestions are not diagnosed root causes."""
import re

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
