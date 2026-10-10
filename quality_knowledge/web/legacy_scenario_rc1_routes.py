"""Mature RC1 scenario capabilities; separate from modern QSV1 workbenches.

Source: #301 legacy restoration. Existing issue/import APIs stay untouched.
"""
import threading
import uuid
from pathlib import Path
from urllib.parse import urlencode
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from quality_knowledge.scenarios import ScenarioRepository
from quality_knowledge.scenario_generation import ScenarioGenerationService

ALLOWED={'.xlsx', '.xlsm'}

def create_legacy_scenario_router(db_path, issue_service, product_repository, templates):
    """Attach unchanged RC1 business flow to standalone legacy host only."""
    router=APIRouter()
    scenario_repo=ScenarioRepository(db_path)
    scenario_generation_svc=ScenarioGenerationService(issue_service, scenario_repo, Path(__file__).resolve().parents[2])
    product_repo=product_repository
    tpl=templates
    @router.get('/quality-scenarios', response_class=HTMLResponse, include_in_schema=False)
    def quality_scenarios(request: Request, product_code: str = '', ipmt: str = '', spdt: str = '', product_model: str = '', industry: str = '', customer_name: str = '', q: str = '', status: str = '', generation_id: str = '', activity_code: str = '', experience_code: str = '', qiu_code: str = '', quality_code: str = '', typical_problem_code: str = '', environment_code: str = '', deleted: int = 0, protected: int = 0):
        taxonomy=scenario_repo.taxonomy()
        filters={'product_code':product_code,'ipmt':ipmt,'spdt':spdt,'product_model':product_model,'industry':industry,'customer_name':customer_name,'q':q,'status':status,'generation_id':generation_id,'activity_code':activity_code,'experience_code':experience_code,'qiu_code':qiu_code,'quality_code':quality_code,'typical_problem_code':typical_problem_code,'environment_code':environment_code}
        products=product_repo.list();product_labels={x['product_code']:x['product_name'] for x in products};items=scenario_repo.scenarios(**filters)
        grouped={code:[] for code in product_labels}
        for item in items:
            code=item.get('product_code') or 'UNCONFIGURED';item['product_name']=product_labels.get(code,code or '未配置产品');grouped.setdefault(code,[]).append(item)
        product_groups=[{'product_code':code,'product_name':product_labels.get(code,code or '未配置产品'),'items':rows} for code,rows in grouped.items() if rows]
        return tpl.TemplateResponse(request,'quality_scenarios.html',{'items':items,'product_groups':product_groups,'products':products,'options':scenario_repo.scope_options(),'filters':filters,'taxonomy':taxonomy,'lifecycle_labels':{x['lifecycle_code']:x['label_zh'] for x in taxonomy['lifecycles']},'activity_labels':{x['activity_code']:x['label_zh'] for x in taxonomy['activities']},'generation':scenario_repo.generation(generation_id) if generation_id else None,'deleted':deleted,'protected':protected})

    @router.post('/quality-scenarios/delete-generated', include_in_schema=False)
    def quality_scenario_delete_generated(product_code: str = Form(''), ipmt: str = Form(''), spdt: str = Form(''), product_model: str = Form(''), industry: str = Form(''), customer_name: str = Form(''), q: str = Form(''), status: str = Form(''), generation_id: str = Form(''), activity_code: str = Form(''), experience_code: str = Form(''), qiu_code: str = Form(''), quality_code: str = Form(''), typical_problem_code: str = Form(''), environment_code: str = Form('')):
        filters={'product_code':product_code,'ipmt':ipmt,'spdt':spdt,'product_model':product_model,'industry':industry,'customer_name':customer_name,'q':q,'status':status,'generation_id':generation_id,'activity_code':activity_code,'experience_code':experience_code,'qiu_code':qiu_code,'quality_code':quality_code,'typical_problem_code':typical_problem_code,'environment_code':environment_code}
        result=scenario_repo.delete_generated_scenarios(**filters)
        kept={key:value for key,value in filters.items() if value}
        kept.update({'deleted':result['deleted'],'protected':result['protected']})
        return RedirectResponse('/quality-scenarios?'+urlencode(kept),303)

    @router.get('/quality-scenarios/standardize', response_class=HTMLResponse, include_in_schema=False)
    def quality_scenario_standardize_page(request: Request, batch_id: str = ''):
        items=scenario_repo.standardization_items()
        counts={key:sum(1 for x in items if x['quality_classification_status']==key) for key in ('NOT_ANALYZED','RUNNING','PENDING_CONFIRMATION','CONFIRMED','FAILED')}
        batch=scenario_repo.standardization_batch(batch_id) if batch_id else None
        return tpl.TemplateResponse(request,'quality_scenario_standardize.html',{'items':items,'counts':counts,'running':bool(counts['RUNNING']) or bool(batch and batch['status'] in {'QUEUED','RUNNING'}),'batch':batch,'batches':scenario_repo.standardization_batches()})

    @router.post('/quality-scenarios/standardize', include_in_schema=False)
    def quality_scenario_standardize(selected_ids: list[str] = Form([])):
        if not selected_ids:raise HTTPException(400,'QUALITY_SCENARIO_SELECTION_REQUIRED')
        batch_id=f'QSB-{uuid.uuid4().hex}';scenario_repo.create_standardization_batch(batch_id,list(selected_ids))
        threading.Thread(target=scenario_generation_svc.standardize_existing,args=(list(selected_ids),batch_id),daemon=True,name=f'scenario-standardize-{batch_id[-8:]}').start()
        return RedirectResponse(f'/quality-scenarios/standardize?batch_id={batch_id}',303)

    @router.post('/quality-scenarios/standardize/{batch_id}/retry', include_in_schema=False)
    def quality_scenario_standardize_retry(batch_id: str):
        batch=scenario_repo.standardization_batch(batch_id)
        if not batch:raise HTTPException(404,'QUALITY_STANDARDIZATION_BATCH_NOT_FOUND')
        ids=[x['scenario_id'] for x in batch['items'] if x['status']=='FAILED']
        if not ids:raise HTTPException(400,'NO_FAILED_STANDARDIZATION_ITEMS')
        new_id=f'QSB-{uuid.uuid4().hex}';scenario_repo.create_standardization_batch(new_id,ids,'WEB_RETRY')
        threading.Thread(target=scenario_generation_svc.standardize_existing,args=(ids,new_id),daemon=True,name=f'scenario-standardize-{new_id[-8:]}').start()
        return RedirectResponse(f'/quality-scenarios/standardize?batch_id={new_id}',303)

    @router.get('/quality-scenarios/{scenario_id}/capabilities', response_class=HTMLResponse, include_in_schema=False)
    def quality_scenario_capabilities(request: Request, scenario_id: str):
        item=scenario_repo.scenario(scenario_id)
        if not item:raise HTTPException(404,'QUALITY_SCENARIO_NOT_FOUND')
        return tpl.TemplateResponse(request,'quality_scenario_capabilities.html',{'item':item,'dictionary':scenario_repo.capability_dictionary()})

    @router.post('/quality-scenarios/{scenario_id}/capabilities', include_in_schema=False)
    def quality_scenario_capability_save(scenario_id: str, capability_axis: str = Form(...), capability_code: str = Form(...), gap_description: str = Form(...), source_basis: str = Form(''), improvement_action: str = Form(''), verification_metric: str = Form(''), priority: str = Form('P1'), status: str = Form('OPEN')):
        try:scenario_repo.save_scenario_capability_gap(scenario_id,locals())
        except KeyError:raise HTTPException(404,'QUALITY_SCENARIO_NOT_FOUND')
        except ValueError as error:raise HTTPException(400,str(error)) from error
        return RedirectResponse(f'/quality-scenarios/{scenario_id}/capabilities',303)

    @router.post('/quality-scenarios/{scenario_id}/capabilities/{gap_id}/delete', include_in_schema=False)
    def quality_scenario_capability_delete(scenario_id: str,gap_id: str):
        try:scenario_repo.delete_scenario_capability_gap(scenario_id,gap_id)
        except KeyError:raise HTTPException(404,'SCENARIO_CAPABILITY_GAP_NOT_FOUND')
        return RedirectResponse(f'/quality-scenarios/{scenario_id}/capabilities',303)

    @router.get('/quality-scenarios/generate', response_class=HTMLResponse, include_in_schema=False)
    def quality_scenario_generate_page(request: Request, product_code: str = '', start_month: str = '', end_month: str = '', preview: int = 0, job_id: str = '', ipmt: str = '', spdt: str = '', product_model: str = '', product_series: str = '', industry: str = '', customer: str = '', year: str = ''):
        from quality_knowledge.scenario_sources import scene_source_records,operation_scope_counts
        filters={'product_code':product_code,'start_month':start_month,'end_month':end_month,'source':'operations','ipmt':ipmt,'spdt':spdt,'product_model':product_model,'product_series':product_series,'industry':industry,'customer':customer,'year':year}
        options=scene_source_records(scenario_generation_svc,'operations',{'product_code':product_code},metadata_only=True)
        selected={key:filters.get(key,'') for key in ('industry','customer','ipmt','spdt','product_model','product_series','year')}
        choices={}
        for key in selected:
            counts={}
            for row in options:
                if any(value and other!=key and str(row.get(other) or '')!=str(value) for other,value in selected.items()):continue
                label=str(row.get(key) or '').strip()
                if label:counts[label]=counts.get(label,0)+1
            choices[key]=[{'label':label,'count':count} for label,count in sorted(counts.items(),key=lambda item:(-item[1],item[0]))]
        scope=None
        if preview:
            rows=scene_source_records(scenario_generation_svc,'operations',filters)
            analysed=sum(bool(x['leakage_analysis']) for x in rows)
            scope={'items':rows,'issue_count':len(rows),'analysed_count':analysed,'coverage_rate':round(analysed*100/len(rows),1) if rows else 0,
                   'linked_cs_count':sum(bool(x.get('cs_material_id')) for x in rows),
                   'missing_leakage_count':sum(bool(x.get('missing_leakage')) for x in rows)}
        counts=operation_scope_counts(scenario_generation_svc,filters)
        return tpl.TemplateResponse(request,'quality_scenario_generate.html',{'products':product_repo.list(),'generations':scenario_repo.generations(),'result':None,'scope':scope,'scope_counts':counts,'choices':choices,'job':scenario_repo.generation(job_id) if job_id else None,'filters':filters})

    @router.get('/quality-scenarios/insights', response_class=HTMLResponse, include_in_schema=False)
    def quality_scenario_insights(request: Request, status: str = '', product_code: str = ''):
        from quality_knowledge.scenario_assets import ScenarioAssets
        from quality_knowledge.scenario_decisions import decision_digest
        products=product_repo.list();labels={x['product_code']:x['product_name'] for x in products}
        decision=decision_digest(ScenarioAssets(scenario_repo).report({'status':status,'business':product_code}),scenario_repo)
        insights=scenario_repo.insights(status=status,product_code=product_code)
        for row in insights['product_rows']:row['product_name']=labels.get(row['product_code'],row['product_code'])
        return tpl.TemplateResponse(request,'quality_scenario_insights.html',{'insights':insights,'status':status,'product_code':product_code,'products':products,'decision':decision})

    @router.post('/quality-scenarios/generate', response_class=HTMLResponse, include_in_schema=False)
    def quality_scenario_generate(request: Request, product_code: str = Form(...), start_month: str = Form(''), end_month: str = Form(''), selected_ids: list[str] = Form([]), ipmt: str = Form(''), spdt: str = Form(''), product_model: str = Form(''), product_series: str = Form(''), industry: str = Form(''), customer: str = Form(''), year: str = Form('')):
        if not selected_ids:raise HTTPException(400,'SCENARIO_SOURCE_SELECTION_REQUIRED')
        if not scenario_repo.taxonomy_active(product_code):raise HTTPException(400,f'产品 {product_code} 尚未配置并激活场景词典，请先到场景词典配置中创建并激活')
        generation_id=f'QSG-{uuid.uuid4().hex}'
        from quality_knowledge.scenario_sources import scene_source_records
        rows=scene_source_records(scenario_generation_svc,'operations',{'product_code':product_code,'ipmt':ipmt,'spdt':spdt,'product_model':product_model,'product_series':product_series,'industry':industry,'customer':customer,'year':year,'start_month':start_month,'end_month':end_month},selected_ids)
        if {x['knowledge_id'] for x in rows}!=set(selected_ids):raise HTTPException(409,'选中数据已变化或不属于当前软件考核筛选范围，请重新预览')
        scenario_generation_svc.save_source_snapshot(generation_id,rows)
        scenario_repo.create_generation(generation_id,product_code,start_month,end_month,len(selected_ids),'WEB_USER')
        threading.Thread(target=scenario_generation_svc.run_job,args=(generation_id,product_code,start_month,end_month,list(selected_ids)),daemon=True,name=f'scenario-{generation_id[-8:]}').start()
        return RedirectResponse(f'/quality-scenarios/generate?job_id={generation_id}',303)

    @router.get('/api/quality-scenario-generations/{generation_id}')
    def quality_scenario_generation_status(generation_id: str):
        job=scenario_repo.generation(generation_id)
        if not job:raise HTTPException(404,'SCENARIO_GENERATION_NOT_FOUND')
        return job

    @router.get('/quality-scenarios/generations/{generation_id}/issues', response_class=HTMLResponse, include_in_schema=False)
    def quality_scenario_generation_issues(request: Request, generation_id: str):
        job=scenario_repo.generation(generation_id)
        if not job:raise HTTPException(404,'SCENARIO_GENERATION_NOT_FOUND')
        snapshot={row['knowledge_id']:row for row in scenario_generation_svc.source_snapshot(generation_id)}
        items=[]
        for row in scenario_repo.issue_classifications(generation_id):
            source=snapshot.get(row['knowledge_id'],{})
            items.append({**row,'source_workbench':source.get('source_workbench') or '',
                          'source_material_id':source.get('source_material_id') or ''})
        return tpl.TemplateResponse(request,'quality_scenario_generation_issues.html',{'job':job,'items':items})

    @router.post('/quality-scenarios/generations/delete-finished', include_in_schema=False)
    def quality_scenario_generation_delete_finished():
        deleted=scenario_repo.delete_finished_generations()
        return RedirectResponse(f'/quality-scenarios/generate?records_deleted={deleted}',303)

    @router.post('/quality-scenarios/generations/{generation_id}/delete', include_in_schema=False)
    def quality_scenario_generation_delete(generation_id: str):
        try:scenario_repo.delete_generation(generation_id)
        except KeyError:raise HTTPException(404,'SCENARIO_GENERATION_NOT_FOUND')
        except ValueError as error:raise HTTPException(409,str(error)) from error
        return RedirectResponse('/quality-scenarios/generate?records_deleted=1',303)

    @router.post('/quality-scenarios/generations/{generation_id}/retry', include_in_schema=False)
    def quality_scenario_generation_retry(generation_id: str, selected_ids: list[str] = Form([])):
        job=scenario_repo.generation(generation_id)
        if not job:raise HTTPException(404,'SCENARIO_GENERATION_NOT_FOUND')
        if not selected_ids:selected_ids=[x['knowledge_id'] for x in scenario_repo.issue_classifications(generation_id) if x['status'] in {'FAILED','REVIEW_REQUIRED','PENDING'}]
        if not selected_ids:raise HTTPException(400,'NO_RETRYABLE_SCENARIO_ISSUES')
        new_id=f'QSG-{uuid.uuid4().hex}';scenario_repo.create_generation(new_id,job['product_code'],job['start_month'],job['end_month'],len(selected_ids),'WEB_RETRY')
        snapshot=scenario_generation_svc.source_snapshot(generation_id)
        if snapshot:
            retry_rows=[r for r in snapshot if r['knowledge_id'] in selected_ids]
            if len(retry_rows)!=len(set(selected_ids)):raise HTTPException(400,'重试问题不属于原任务')
            scenario_generation_svc.save_source_snapshot(new_id,retry_rows)
        threading.Thread(target=scenario_generation_svc.run_job,args=(new_id,job['product_code'],job['start_month'],job['end_month'],selected_ids),daemon=True,name=f'scenario-{new_id[-8:]}').start()
        return RedirectResponse(f'/quality-scenarios/generate?job_id={new_id}',303)

    @router.get('/quality-scenarios/{scenario_id}', response_class=HTMLResponse, include_in_schema=False)
    @router.get('/quality-scenarios/new', response_class=HTMLResponse, include_in_schema=False)
    def quality_scenario_edit(request: Request, scenario_id: str = ''):
        item=scenario_repo.scenario(scenario_id) if scenario_id else None
        if scenario_id and not item:raise HTTPException(404,'QUALITY_SCENARIO_NOT_FOUND')
        product_code=(item or {}).get('product_code') or 'PLC';taxonomy=scenario_repo.taxonomy(product_code=product_code) or scenario_repo.taxonomy(product_code='PLC')
        return tpl.TemplateResponse(request,'quality_scenario_edit.html',{'item':item or {'scenario_id':'','scenario_code':'','name':'','product_code':product_code,'taxonomy_version_id':taxonomy['version_id'] if taxonomy else '','lifecycle_code':'','activity_code':'','scenario_chain':'','experience_requirement':'','concern_points':'','customer_perception':'','primary_typical_problem_code':'','secondary_typical_problem_codes':[],'primary_quality_concern_code':'','secondary_quality_concern_codes':[],'primary_customer_experience_statement':'','secondary_customer_experience_statements':[],'operating_environment':'','operating_condition':'','duration_frequency':'','disturbances':'','extreme_conditions':'','environment_condition_codes':[],'primary_experience_code':'','secondary_experience_codes':[],'quality_in_use_codes':[],'primary_quality_characteristic_code':'','secondary_quality_characteristic_codes':[],'quality_subcharacteristic_codes':[],'quality_classification_status':'PENDING_CONFIRMATION','quality_attribute':'','quality_subcharacteristic':'','failure_mode':'','failure_mechanism':'','trigger_conditions':'','preconditions':'','participating_systems':'','system_scale':'','user_type':'','affected_object':'','business_impact':'','recovery_method':'','applicable_boundary':'','validation_direction':'','measurement_suggestion':'','status':'DRAFT','scopes':{},'industry_variants':[]},'taxonomy':taxonomy,'options':scenario_repo.scope_options(),'quality_models':scenario_repo.quality_models(),'semantics':scenario_repo.semantic_dictionary(product_code)})

    @router.post('/quality-scenarios/save', include_in_schema=False)
    def quality_scenario_save(scenario_id: str = Form(''), scenario_code: str = Form(...), name: str = Form(...), product_code: str = Form('PLC'), taxonomy_version_id: str = Form(''), lifecycle_code: str = Form(''), activity_code: str = Form(''), customer_perception: str = Form(''), primary_typical_problem_code: str = Form(''), secondary_typical_problem_codes: list[str] = Form([]), primary_quality_concern_code: str = Form(''), secondary_quality_concern_codes: list[str] = Form([]), primary_customer_experience_statement: str = Form(''), secondary_customer_experience_statements: str = Form(''), operating_environment: str = Form(''), operating_condition: str = Form(''), duration_frequency: str = Form(''), disturbances: str = Form(''), extreme_conditions: str = Form(''), environment_condition_codes: list[str] = Form([]), primary_experience_code: str = Form(''), secondary_experience_codes: list[str] = Form([]), quality_in_use_codes: list[str] = Form([]), primary_quality_characteristic_code: str = Form(''), secondary_quality_characteristic_codes: list[str] = Form([]), quality_subcharacteristic_codes: list[str] = Form([]), quality_classification_status: str = Form('PENDING_CONFIRMATION'), confirmed_by: str = Form('WEB_USER'), experience_requirement: str = Form(''), concern_points: str = Form(''), quality_attribute: str = Form(''), quality_subcharacteristic: str = Form(''), failure_mode: str = Form(''), failure_mechanism: str = Form(''), trigger_conditions: str = Form(''), preconditions: str = Form(''), participating_systems: str = Form(''), system_scale: str = Form(''), user_type: str = Form(''), affected_object: str = Form(''), business_impact: str = Form(''), recovery_method: str = Form(''), applicable_boundary: str = Form(''), validation_direction: str = Form(''), measurement_suggestion: str = Form(''), status: str = Form('DRAFT'), ipmt: list[str] = Form([]), spdt: list[str] = Form([]), product_model: list[str] = Form([]), industry: list[str] = Form([]), customer_name: list[str] = Form([]), customer_level: list[str] = Form([]), customer_status: list[str] = Form([]), occurrence_phase: list[str] = Form([])):
        saved=scenario_repo.save_scenario(scenario_id,locals(),{'IPMT':ipmt,'SPDT':spdt,'PRODUCT_MODEL':product_model,'INDUSTRY':industry,'CUSTOMER_NAME':customer_name,'CUSTOMER_LEVEL':customer_level,'CUSTOMER_STATUS':customer_status,'OCCURRENCE_PHASE':occurrence_phase})
        return RedirectResponse(f'/quality-scenarios/{saved}',303)

    @router.get('/settings/scenario-semantics', response_class=HTMLResponse, include_in_schema=False)
    def scenario_semantics_settings(request: Request, product_code: str = 'PLC'):
        return tpl.TemplateResponse(request,'scenario_semantics.html',{'dictionary':scenario_repo.semantic_dictionary(product_code),'product_code':product_code,'products':product_repo.list()})

    @router.post('/settings/scenario-semantics/save', include_in_schema=False)
    def scenario_semantics_save(term_id: str = Form(''), term_type: str = Form(...), term_code: str = Form(...), label_zh: str = Form(...), definition: str = Form(...), inclusion_criteria: str = Form(''), exclusion_criteria: str = Form(''), aliases: str = Form(''), product_code: str = Form(''), status: str = Form('ACTIVE')):
        try:scenario_repo.save_semantic_term(locals())
        except ValueError as error:raise HTTPException(400,str(error)) from error
        return RedirectResponse(f'/settings/scenario-semantics?product_code={product_code}',303)

    @router.post('/settings/scenario-semantics/{term_id}/review', include_in_schema=False)
    def scenario_semantics_review(term_id: str, action: str = Form(...), target_code: str = Form(''), product_code: str = Form('PLC')):
        try:scenario_repo.review_semantic_term(term_id,action,target_code)
        except KeyError:raise HTTPException(404,'SEMANTIC_TERM_NOT_FOUND')
        except ValueError as error:raise HTTPException(400,str(error)) from error
        return RedirectResponse(f'/settings/scenario-semantics?product_code={product_code}',303)

    @router.post('/quality-scenarios/{scenario_id}/delete', include_in_schema=False)
    def quality_scenario_delete(scenario_id: str):
        try:scenario_repo.delete_scenario(scenario_id)
        except KeyError:raise HTTPException(404,'QUALITY_SCENARIO_NOT_FOUND')
        return RedirectResponse('/quality-scenarios',303)

    @router.get('/settings/scenario-taxonomy', response_class=HTMLResponse, include_in_schema=False)
    def scenario_taxonomy_settings(request: Request, product_code: str = 'PLC'):
        taxonomy=scenario_repo.taxonomy(product_code=product_code)
        return tpl.TemplateResponse(request,'scenario_taxonomy.html',{'taxonomy':taxonomy,'versions':scenario_repo.versions(product_code),'products':product_repo.list(),'product_code':product_code})

    @router.post('/settings/scenario-taxonomy/draft', include_in_schema=False)
    def scenario_taxonomy_draft(product_code: str = Form(...), source_product_code: str = Form('')):
        scenario_repo.create_draft(product_code,source_product_code);return RedirectResponse(f'/settings/scenario-taxonomy?product_code={product_code}',303)

    @router.post('/settings/scenario-taxonomy/lifecycle', include_in_schema=False)
    def scenario_lifecycle_save(version_id: str = Form(...), lifecycle_code: str = Form(...), label_zh: str = Form(...), description: str = Form(''), value_statement: str = Form(''), objective: str = Form(''), enabled: str = Form('')):
        scenario_repo.save_lifecycle(version_id,lifecycle_code,label_zh,description,enabled=='on',value_statement,objective);product=scenario_repo.taxonomy(version_id).get('product_code','PLC');return RedirectResponse(f'/settings/scenario-taxonomy?product_code={product}#lifecycles',303)

    @router.post('/settings/scenario-taxonomy/activity', include_in_schema=False)
    def scenario_activity_save(version_id: str = Form(...), lifecycle_code: str = Form(...), activity_code: str = Form(...), label_zh: str = Form(...), chain_text: str = Form(''), description: str = Form(''), participating_systems: str = Form(''), objective: str = Form(''), enabled: str = Form('')):
        scenario_repo.save_activity(version_id,lifecycle_code,activity_code,label_zh,chain_text,description,enabled=='on',participating_systems,objective);product=scenario_repo.taxonomy(version_id).get('product_code','PLC');return RedirectResponse(f'/settings/scenario-taxonomy?product_code={product}#business-activities',303)

    @router.post('/settings/scenario-taxonomy/activities/bulk', include_in_schema=False)
    async def scenario_activities_bulk(request: Request):
        form=await request.form();version_id=str(form.get('version_id') or '')
        codes=[str(x) for x in form.getlist('activity_code')];enabled=set(str(x) for x in form.getlist('enabled_code'))
        fields={name:[str(x) for x in form.getlist(name)] for name in ('lifecycle_code','label_zh','chain_text','description','participating_systems','objective')}
        items=[]
        for index,code in enumerate(codes):
            items.append({'activity_code':code,'enabled':code in enabled,**{name:(values[index] if index<len(values) else '') for name,values in fields.items()}})
        scenario_repo.save_activities(version_id,items);product=scenario_repo.taxonomy(version_id).get('product_code','PLC')
        return RedirectResponse(f'/settings/scenario-taxonomy?product_code={product}&saved={len(items)}#business-activities',303)

    @router.post('/settings/scenario-taxonomy/import', include_in_schema=False)
    def scenario_taxonomy_import(file: UploadFile = File(...), product_code: str = Form(...)):
        if Path(file.filename or '').suffix.lower() not in ALLOWED:raise HTTPException(400,'仅支持 .xlsx 或 .xlsm 模板')
        version_id=scenario_repo.create_draft(product_code)
        try:result=scenario_repo.import_taxonomy_workbook(version_id,file.file)
        except (KeyError,ValueError) as error:raise HTTPException(400,f'场景词典模板校验失败：{error}') from error
        finally:file.file.close()
        return RedirectResponse(f"/settings/scenario-taxonomy?product_code={product_code}&imported={result['activity_count']}#business-activities",303)

    @router.post('/settings/scenario-taxonomy/{version_id}/activate', include_in_schema=False)
    def scenario_taxonomy_activate(version_id: str):
        product=scenario_repo.taxonomy(version_id).get('product_code','PLC');scenario_repo.activate(version_id);return RedirectResponse(f'/settings/scenario-taxonomy?product_code={product}',303)



    return router, scenario_repo, scenario_generation_svc
