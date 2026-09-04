from urllib.parse import urlencode
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse
from quality_knowledge.scenario_assets import ScenarioAssets, CONTEXT_FIELDS, METRIC_FIELDS

DIMENSIONS={'industry':'行业','customer':'客户','product':'产品型号','business':'产品/业务','lifecycle':'使用生命周期',
            'activity':'业务活动','scale':'系统规模','environment':'环境/工况','concern':'客户质量关注点',
            'quality':'质量属性','period':'场景问题时间','scenario':'质量场景'}

def create_asset_router(repository,templates):
    router=APIRouter();service=ScenarioAssets(repository)

    @router.get('/quality-scenario-assets')
    def overview(request:Request):
        filters=dict(request.query_params)
        try:report=service.report(filters)
        except ValueError as e:raise HTTPException(400,str(e))
        x=filters.get('x','industry');y=filters.get('y','scenario')
        names={a['scenario_id']:a['name'] for a in service.catalog()}
        taxonomy_labels={}
        for a in service.catalog():
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
        return templates.TemplateResponse(request,'scenario_asset_overview.html',{'report':report,'filters':filters,'dimensions':DIMENSIONS,'x':x,'y':y,'matrix_rows':matrix_rows,'columns':columns,'taxonomy_labels':taxonomy_labels})

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
