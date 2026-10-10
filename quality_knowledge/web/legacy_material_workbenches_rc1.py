"""Historical mature RC1 ITR, resolution, software operations and source settings.
Only for isolated copy; not the discarded QSV1 pipeline. Source ce2eca157c4403b97036dd66e5218cd74fe53700.
"""
from __future__ import annotations
import tempfile
from pathlib import Path
from urllib.parse import urlencode
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from quality_knowledge.legacy_materials_rc1 import MaterialRepository, MaterialImportService
from .missed_test_adapter import build_missed_test_rows
ALLOWED={'.xlsx','.xlsm'}

def create_legacy_material_router(db_path,tpl,issue_service):
    router=APIRouter()
    material_repo=MaterialRepository(db_path)
    material_svc=MaterialImportService(material_repo)
    material_workbenches={
        'itr':{'group_code':'ITR','title':'ITR问题工作台','description':'现场问题、客户影响、发生阶段与处理过程','scope':'软件 / 硬件 / 机械 / 跨领域'},
        'cs':{'group_code':'ITR-CS','title':'ITR彻底解决工作台','description':'责任认定、技术根因、永久措施、MRC与标准化','scope':'软件 / 硬件 / 机械 / 跨领域'},
        'software-operations':{'group_code':'SW-OPS','title':'软件问题考核工作台','description':'软件问题KPI、审核状态、责任单位与运营考核','scope':'仅软件'},
    }

    @router.get('/materials', include_in_schema=False)
    def materials_root():
        return RedirectResponse('/materials/itr',303)

    @router.get('/materials/{workbench}', response_class=HTMLResponse, include_in_schema=False)
    def materials_page(request: Request, workbench: str, q: str = '', domain: str = '', month: str = '', year: str = '', industry: str = '', customer: str = '', ipmt: str = '', spdt: str = '', product_model: str = '', product_series: str = '', page: int = 1, cleaned: int = 0, protected: int = 0):
        workspace=material_workbenches.get(workbench)
        if not workspace:raise HTTPException(404,'MATERIAL_WORKBENCH_NOT_FOUND')
        filters={'q':q,'domain':domain,'month':month,'year':year,'industry':industry,'customer':customer,
                 'ipmt':ipmt,'spdt':spdt,'product_model':product_model,'product_series':product_series}
        result=material_repo.search_materials(workspace['group_code'],page=page,**filters)
        return tpl.TemplateResponse(request, 'materials.html', {
            'groups': material_repo.groups(False), 'items': result['items'], 'listing':result,
            'filters':filters,'page_query':urlencode({k:v for k,v in filters.items() if v}),
            'group_code': workspace['group_code'], 'result': None, 'workbench':workbench, 'workspace':workspace,
            'duplicates':material_repo.duplicate_summary(workspace['group_code']),'cleanup_result':{'deleted':cleaned,'protected':protected} if cleaned or protected else None,
        })

    @router.get('/software-operation-distribution', response_class=HTMLResponse, include_in_schema=False)
    def software_operation_distribution(request:Request, ipmt: str = '', spdt: str = '', product_model: str = '', product_series: str = '', year: str = '', month: str = ''):
        filters={'ipmt':ipmt,'spdt':spdt,'product_model':product_model,'product_series':product_series,'year':year,'month':month}
        report=material_repo.software_operation_distribution(**filters)
        return tpl.TemplateResponse(request,'software_operation_distribution.html',{'filters':filters,'report':report})

    @router.get('/materials/{workbench}/data-cleanup', response_class=HTMLResponse, include_in_schema=False)
    def material_data_cleanup_page(request:Request,workbench:str,field_name:str='',operator:str='HAS_FIELD',value:str='',preview:int=0,deleted:int=0,protected:int=0):
        workspace=material_workbenches.get(workbench)
        if not workspace:raise HTTPException(404,'MATERIAL_WORKBENCH_NOT_FOUND')
        criteria={'field_name':field_name,'operator':operator,'value':value}
        result=None;error=''
        if preview:
            try:result=material_repo.invalid_import_preview(workspace['group_code'],**criteria)
            except ValueError as exc:error=str(exc)
        return tpl.TemplateResponse(request,'material_data_cleanup.html',{'workspace':workspace,'workbench':workbench,
            'fields':material_repo.raw_field_catalog(workspace['group_code']),'criteria':criteria,'preview':result,'error':error,
            'cleanup_result':{'deleted':deleted,'protected':protected} if deleted or protected else None})

    @router.post('/materials/{workbench}/data-cleanup', include_in_schema=False)
    def material_data_cleanup_execute(workbench:str,field_name:str=Form(...),operator:str=Form(...),value:str=Form(''),confirmed:str=Form('')):
        workspace=material_workbenches.get(workbench)
        if not workspace:raise HTTPException(404,'MATERIAL_WORKBENCH_NOT_FOUND')
        if confirmed!='yes':raise HTTPException(400,'请先预览并确认删除范围')
        try:result=material_repo.cleanup_invalid_import(workspace['group_code'],field_name=field_name,operator=operator,value=value)
        except ValueError as exc:raise HTTPException(400,str(exc)) from exc
        query=urlencode({'deleted':result['deleted'],'protected':result['protected']})
        return RedirectResponse(f'/materials/{workbench}/data-cleanup?{query}',303)

    @router.get('/materials/{workbench}/{material_id}', response_class=HTMLResponse, include_in_schema=False)
    def material_detail(request: Request, workbench: str, material_id: str):
        workspace=material_workbenches.get(workbench)
        if not workspace:raise HTTPException(404,'MATERIAL_WORKBENCH_NOT_FOUND')
        item=material_repo.material(material_id)
        if not item or item['group_code']!=workspace['group_code']:raise HTTPException(404,'MATERIAL_NOT_FOUND')
        return tpl.TemplateResponse(request,'material_detail.html',{'item':item,'workspace':workspace,'workbench':workbench,'related':material_repo.related_materials(item['canonical_itr'],material_id)})

    @router.post('/materials/{workbench}/{material_id}/review', include_in_schema=False)
    def material_review_save(workbench: str, material_id: str, review_status: str = Form('DRAFT'), analysis_summary: str = Form(''), root_cause: str = Form(''), improvement_action: str = Form(''), reviewer: str = Form('')):
        workspace=material_workbenches.get(workbench)
        item=material_repo.material(material_id)
        if not workspace or not item or item['group_code']!=workspace['group_code']:raise HTTPException(404,'MATERIAL_NOT_FOUND')
        try:material_repo.save_review(material_id,review_status=review_status,analysis_summary=analysis_summary,root_cause=root_cause,improvement_action=improvement_action,reviewer=reviewer)
        except ValueError as error:raise HTTPException(400,str(error)) from error
        return RedirectResponse(f'/materials/{workbench}/{material_id}#independent-review',303)

    @router.post('/materials/import', response_class=HTMLResponse, include_in_schema=False)
    def materials_import(request: Request, file: UploadFile = File(...), group_code: str = Form(...), header_rows: int = Form(2), workbench: str = Form(...), reporting_year: str = Form('')):
        workspace=material_workbenches.get(workbench)
        if not workspace or workspace['group_code']!=group_code:raise HTTPException(400,'WORKBENCH_GROUP_MISMATCH')
        if Path(file.filename or '').suffix.lower() not in ALLOWED:
            raise HTTPException(400, '仅支持 .xlsx / .xlsm')
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/Path(file.filename or 'upload.xlsx').name;path.write_bytes(file.file.read())
            try: result=material_svc.import_file(path,group_code,header_rows,reporting_year=reporting_year)
            except ValueError as error: raise HTTPException(400,str(error)) from error
        listing=material_repo.search_materials(group_code)
        return tpl.TemplateResponse(request, 'materials.html', {
            'groups': material_repo.groups(False), 'items': listing['items'],
            'listing':listing,'filters':{'q':'','domain':'','month':'','year':'','industry':'','customer':'','ipmt':'','spdt':'','product_model':'','product_series':''},'page_query':'',
            'group_code': group_code, 'result': result, 'workbench':workbench, 'workspace':workspace,
            'duplicates':material_repo.duplicate_summary(group_code),'cleanup_result':None,
        })

    @router.post('/materials/{workbench}/cleanup-duplicates', include_in_schema=False)
    def material_cleanup_duplicates(workbench:str):
        workspace=material_workbenches.get(workbench)
        if not workspace:raise HTTPException(404,'MATERIAL_WORKBENCH_NOT_FOUND')
        result=material_repo.cleanup_duplicates(workspace['group_code'])
        return RedirectResponse(f"/materials/{workbench}?cleaned={result['deleted']}&protected={result['protected']}",303)

    @router.post('/materials/software-operations/batch-year', include_in_schema=False)
    def software_operation_batch_year(material_ids: list[str] = Form(default=[]), reporting_year: str = Form(...)):
        try:material_repo.set_reporting_year(material_ids,reporting_year)
        except ValueError as error:raise HTTPException(400,str(error)) from error
        return RedirectResponse(f'/materials/software-operations?year={reporting_year}',303)

    @router.get('/settings/associations', response_class=HTMLResponse, include_in_schema=False)
    def association_settings(request: Request):
        return tpl.TemplateResponse(request,'association_settings.html',{'rules':material_repo.rules(),'preview':material_repo.link_preview()})

    @router.post('/settings/associations/{rule_id}', include_in_schema=False)
    def association_rule_save(rule_id: str, source_field: str = Form(...), target_field: str = Form(...), transform: str = Form(...), status: str = Form(...)):
        try:material_repo.update_rule(rule_id,source_field=source_field,target_field=target_field,transform=transform,status=status)
        except KeyError:raise HTTPException(404,'RULE_NOT_FOUND')
        except ValueError as error:raise HTTPException(400,str(error)) from error
        material_repo.refresh_links()
        return RedirectResponse('/settings/associations',303)


    @router.get('/missed-test-analysis',response_class=HTMLResponse,include_in_schema=False)
    def missed_test_analysis(request:Request):
        q=(request.query_params.get('q') or '').strip()
        status=(request.query_params.get('analysis_status') or '').strip().upper()
        rows=build_missed_test_rows(issue_service,q=q,analysis_status=status,
                                    detail_prefix='/issues',return_path='/missed-test-analysis',
                                    detail_anchor='causes')
        return tpl.TemplateResponse(request,'missed_test_analysis.html',{
            'items':rows,'total':len(rows),'q':q,'analysis_status':status})
    return router,material_repo
