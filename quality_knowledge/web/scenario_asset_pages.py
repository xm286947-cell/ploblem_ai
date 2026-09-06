from urllib.parse import urlencode
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse
from quality_knowledge.scenario_assets import ScenarioAssets, CONTEXT_FIELDS, METRIC_FIELDS
from quality_knowledge.scenario_decisions import decision_digest

DIMENSIONS={'industry':'行业','customer':'客户','product':'产品型号','business':'产品/业务','lifecycle':'使用生命周期',
            'activity':'业务活动','scale':'系统规模','environment':'环境/工况','concern':'客户质量关注点',
            'quality':'质量属性','period':'场景问题时间','scenario':'质量场景'}

def create_asset_router(repository,templates,generation=None):
    router=APIRouter();service=ScenarioAssets(repository)
    from quality_knowledge.scenario_interpretation import ScenarioInterpretation
    interpreter=ScenarioInterpretation(service,generation) if generation else None

    @router.post('/quality-scenario-interpretations')
    async def generate_interpretation(request:Request):
        if interpreter is None:raise HTTPException(503,'综合解读服务未配置')
        data=dict(await request.form())
        try:jid=interpreter.start(data,refresh=data.get('refresh')=='1')
        except ValueError as e:raise HTTPException(400,str(e))
        return RedirectResponse('/quality-scenario-interpretations/'+jid,303)

    @router.get('/quality-scenario-interpretations/{jid}')
    def interpretation(request:Request,jid:str):
        job=interpreter.get(jid) if interpreter else None
        if not job:raise HTTPException(404,'解读不存在')
        stale=interpreter.snapshot(job['filters'])[2]!=job['input_hash']
        return templates.TemplateResponse(request,'scenario_interpretation.html',{'job':job,'stale':stale})

    @router.post('/quality-scenario-interpretations/{jid}/stop')
    def stop_interpretation(jid:str):
        if not interpreter or not interpreter.get(jid):raise HTTPException(404,'解读不存在')
        interpreter.stop(jid)
        return RedirectResponse('/quality-scenario-interpretations/'+jid,303)

    @router.get('/api/quality-scenario-interpretations/{jid}')
    def interpretation_status(jid:str):
        job=interpreter.get(jid) if interpreter else None
        if not job:raise HTTPException(404,'解读不存在')
        return {k:job[k] for k in ('status','progress','error')}

    @router.get('/quality-scenario-assets')
    def overview(request:Request):
        filters=dict(request.query_params)
        assets=service.catalog();facts=service.facts()
        try:report=service.report(filters,assets=assets,facts=facts)
        except ValueError as e:raise HTTPException(400,str(e))
        report['decision']=decision_digest(report,repository)
        report['concern_themes']=report['decision']['concerns']
        x=filters.get('x','industry');y=filters.get('y','scenario')
        names={a['scenario_id']:a['name'] for a in assets}
        taxonomy_labels={}
        for a in assets:
            taxonomy=repository.taxonomy(product_code=a['product_code'])
            if taxonomy:
                taxonomy_labels.update({r['activity_code']:r['label_zh'] for r in taxonomy['activities']})
                taxonomy_labels.update({r['lifecycle_code']:r['label_zh'] for r in taxonomy['lifecycles']})
        matrix_rows={};columns={}
        for cell in report['cells']:
            raw_x,raw_y=cell['x'],cell['y']
            params={**filters}
            for dim,val in ((x,cell['x']),(y,cell['y'])):
                if dim=='scenario':
                    params['asset_id']=val
                    continue
                params[dim]=val
            cell['url']='/quality-scenario-assets?'+urlencode(params)
            if x=='scenario':cell['x']=names.get(cell['x'],cell['x'])
            if y=='scenario':cell['y']=names.get(cell['y'],cell['y'])
            if x in ('activity','lifecycle'):cell['x']=taxonomy_labels.get(cell['x'],cell['x'])
            if y in ('activity','lifecycle'):cell['y']=taxonomy_labels.get(cell['y'],cell['y'])
            columns[raw_y]=cell['y']
            matrix_rows.setdefault(raw_x,{'label':cell['x'],'cells':{}})['cells'][raw_y]=cell
        for row in report['distributions']['scenario']:row['label']=names.get(row['label'],row['label'])
        return templates.TemplateResponse(request,'scenario_asset_overview.html',{'report':report,'filters':filters,'dimensions':DIMENSIONS,'x':x,'y':y,'matrix_rows':matrix_rows,'columns':columns,'taxonomy_labels':taxonomy_labels,'interpretation':interpreter.latest(filters) if interpreter else None})

    @router.get('/quality-scenario-assets/portrait')
    def portrait(request:Request):
        filters=dict(request.query_params)
        assets=service.catalog();facts=service.facts()
        try:report=service.portrait(filters,assets=assets,facts=facts)
        except ValueError as e:raise HTTPException(400,str(e))
        report['decision']=decision_digest(report,repository)
        taxonomy_labels={}
        for a in assets:
            taxonomy=repository.taxonomy(product_code=a['product_code'])
            if taxonomy:
                taxonomy_labels.update({r['activity_code']:r['label_zh'] for r in taxonomy['activities']})
                taxonomy_labels.update({r['lifecycle_code']:r['label_zh'] for r in taxonomy['lifecycles']})
        from quality_knowledge.scenario_sources import material_scene_records
        raw_options=material_scene_records(generation,metadata_only=True) if generation else []
        scene_records=service.report({},assets=assets,facts=facts)['records']
        selected={'industry':filters.get('industry',''),'customer':filters.get('customer',''),
                  'product_model':filters.get('product',''),'problem_domain':filters.get('problem_domain','')}
        def related_choices(key):
            counts={}
            for row in raw_options:
                if any(value and other!=key and str(row.get(other) or '')!=str(value) for other,value in selected.items()):continue
                label=str(row.get(key) or '').strip()
                if label:counts[label]=counts.get(label,0)+1
            scene_counts={}
            scene_key='product' if key=='product_model' else key
            for row in scene_records:
                if selected['industry'] and key!='industry' and row.get('industry')!=selected['industry']:continue
                if selected['customer'] and key!='customer' and row.get('customer')!=selected['customer']:continue
                if selected['product_model'] and key!='product_model' and row.get('product')!=selected['product_model']:continue
                label=str(row.get(scene_key) or '').strip()
                if label:scene_counts.setdefault(label,set()).add(row.get('issue_key'))
            return [{'label':label,'problem_count':count,'scene_issue_count':len(scene_counts.get(label,set()))}
                    for label,count in sorted(counts.items(),key=lambda item:(-len(scene_counts.get(item[0],set())),-item[1],item[0]))]
        choices={key:related_choices(key) for key in ('industry','customer','product_model')}
        market_scope=None
        if interpreter and (filters.get('industry') or filters.get('customer')):
            market_rows=interpreter.portrait_scope({**filters,'portrait_mode':'1'})
            market_scope={'items':market_rows,'issue_count':len(market_rows),
                'with_scene_count':sum(bool(x.get('scenarios')) for x in market_rows),
                'without_scene_count':sum(not x.get('scenarios') for x in market_rows),
                'domain_counts':{domain:sum(x.get('problem_domain')==domain for x in market_rows) for domain in ('SOFTWARE','HARDWARE','MECHANICAL','UNKNOWN')}}
        return templates.TemplateResponse(request,'scenario_customer_portrait.html',{
            'report':report,'filters':filters,'dimensions':DIMENSIONS,'taxonomy_labels':taxonomy_labels,
            'choices':choices,'market_scope':market_scope,
            'interpretation':interpreter.latest({**filters,'portrait_mode':'1'}) if interpreter else None})

    @router.get('/quality-scenario-assets/{sid}')
    def detail(request:Request,sid:str):
        asset=next((a for a in service.catalog() if a['scenario_id']==sid),None)
        if not asset:raise HTTPException(404,'场景不存在或已归集到其他场景')
        return templates.TemplateResponse(request,'scenario_asset_detail.html',{'asset':asset,'context_fields':CONTEXT_FIELDS,'metric_fields':METRIC_FIELDS,'candidates':[a for a in service.catalog() if a['scenario_id']!=sid and a['product_code']==asset['product_code']],'facts':service.facts()})

    @router.post('/quality-scenario-assets/{sid}/{action}')
    async def save(request:Request,sid:str,action:str):
        data=await request.form()
        try:
            if action=='context':service.save_context(sid,dict(data))
            elif action=='metric':service.add_metric(sid,dict(data))
            elif action=='group':service.group(sid,data.getlist('member_ids'))
            elif action=='ungroup':service.ungroup(sid,data.getlist('member_ids'))
            else:raise HTTPException(404,'操作不存在')
        except ValueError as e:raise HTTPException(400,str(e))
        return RedirectResponse('/quality-scenario-assets/'+sid,303)
    return router
