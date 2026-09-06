import json

from builder.ai_client import AIResponse
from fastapi.testclient import TestClient

from quality_knowledge.materials import MaterialRepository, normalize_itr
from quality_knowledge.scenario_generation import ScenarioGenerationService
from quality_knowledge.scenario_sources import material_scene_records
from quality_knowledge.scenarios import ScenarioRepository
from quality_knowledge.web.app import create_app
from quality_knowledge.scenario_assets import ScenarioAssets


class NoAnalyses:
    def get_latest_analysis(self, knowledge_id, stage):
        return None


class OneAnalysedIssue:
    def query_issues(self, filters, limit):
        return [{'knowledge_id':'QK-1','business_issue_id':'ITR20260605084','title':'现场状态异常','description':'连续运行后状态异常','month':'6月','severity':'M'}]

    def get_latest_analysis(self, knowledge_id, stage):
        if stage=='occurrence':return {'result':{'root_cause_summary':'状态提交不同步'}}
        return None


class CountingClient:
    model='dedup-model'

    def __init__(self):
        self.calls=0

    def complete(self,messages):
        self.calls+=1
        row=json.loads(messages[-1]['content'])['records'][0]
        payload={'items':[{'name':'长期状态保持一致性','lifecycle_code':'LONG_TERM_OPERATION','activity_code':'RESOURCE_STATE_RETENTION',
            'evidence_issue_ids':[row['knowledge_id']],'lifecycle_reason':'持续运行是触发条件',
            'lifecycle_assessment':{'RUNTIME_EXECUTION':'单次执行证据不足','SYSTEM_INTEGRATION':'无跨系统证据','LONG_TERM_OPERATION':'持续运行后发生'},
            'confidence':0.8}]}
        return AIResponse(json.dumps(payload,ensure_ascii=False),self.model,{})


def test_itr_normalization_only_strips_real_itr_cs_suffix():
    assert normalize_itr(' ITR20260605084cs ')=='ITR20260605084'
    assert normalize_itr('FOCUS')=='FOCUS'


def test_cs_and_itr_become_one_evidence_record_with_cs_fact_priority(tmp_path):
    db=tmp_path/'sources.db';scenes=ScenarioRepository(db);materials=MaterialRepository(db)
    cs,_=materials.add_material(materials.group('ITR-CS'),'ITR20260605084CS',{
        '问题信息_彻底解决单号':'ITR20260605084CS','问题信息_问题描述':'彻底解决单描述',
        '问题信息_IPMT':'FA','问题信息_问题发生时间':'2026-06-05','问题信息_客户行业':'锂电'},'cs.xlsx','sheet',3)
    itr,_=materials.add_material(materials.group('ITR'),'ITR20260605084',{
        '问题信息_ITR单号':'ITR20260605084','问题信息_问题描述':'现场临时描述',
        '问题信息_设备编码':'DEVICE-01','问题信息_故障现象描述':'设备掉电后数据丢失'},'itr.xlsx','sheet',3)
    service=ScenarioGenerationService(NoAnalyses(),scenes,tmp_path,CountingClient())
    rows=material_scene_records(service,{'year':'2026','start_month':'6','end_month':'6'})
    assert len(rows)==1
    row=rows[0]
    assert row['knowledge_id']==cs and row['canonical_itr']=='ITR20260605084'
    assert row['description']=='彻底解决单描述' and row['itr_cs_context']['equipment_code']=='DEVICE-01'
    assert row['source_status']=='CS_ONLY' and row['source_workbench']=='cs'
    assert row['field_sources']['ipmt']=='ITR_CS' and row['field_sources']['equipment_code']=='ITR_SOURCE'
    assert set(row['source_material_ids'])=={cs,itr}


def test_hardware_evidence_is_kept_at_field_level(tmp_path):
    db=tmp_path/'hardware.db';scenes=ScenarioRepository(db);materials=MaterialRepository(db)
    materials.add_material(materials.group('ITR-CS'),'ITR20260605085CS',{
        '问题信息_彻底解决单号':'ITR20260605085CS','问题信息_问题描述':'控制器在高温环境下异常',
        '技术根因分析与纠正_是否器件失效':'是','技术根因分析与纠正_器件类别_单板级':'电容',
        '技术根因分析与纠正_位号':'C18','技术根因分析与纠正_厂家':'测试供应商',
        '技术根因分析与纠正_器件失效模式':'容量衰减'},'cs.xlsx','sheet',3)
    service=ScenarioGenerationService(NoAnalyses(),scenes,tmp_path,CountingClient())
    row=material_scene_records(service)[0]
    assert row['itr_cs_context']['component_category']=='电容'
    assert row['itr_cs_context']['component_refdes']=='C18'
    assert row['field_evidence']['component_failure_mode']['source']=='ITR_CS'
    assert row['field_evidence']['component_vendor']['value']=='测试供应商'
    assert row['problem_domain']=='HARDWARE'
    assert len(material_scene_records(service,{'problem_domain':'HARDWARE'}))==1
    assert material_scene_records(service,{'problem_domain':'SOFTWARE'})==[]


def test_same_evidence_is_reused_across_generation_batches(tmp_path):
    repository=ScenarioRepository(tmp_path/'dedup.db');client=CountingClient()
    service=ScenarioGenerationService(OneAnalysedIssue(),repository,tmp_path,client)
    first=service.generate('PLC','1','12')
    second=service.generate('PLC','1','12')
    assert client.calls==1
    assert first['classified_count']==1 and first['reused_count']==0
    assert second['classified_count']==0 and second['reused_count']==1
    assert second['scenario_ids']==first['scenario_ids']
    ledger=repository.issue_classifications(second['generation_id'])
    assert ledger[0]['status']=='REUSED' and '直接复用' in ledger[0]['error_message']


def test_generate_page_exposes_cs_itr_source_and_material_link(tmp_path):
    db=tmp_path/'web.db';client=TestClient(create_app(db));materials=MaterialRepository(db)
    materials.add_material(materials.group('ITR-CS'),'ITR20260605084CS',{
        '问题信息_彻底解决单号':'ITR20260605084CS','问题信息_问题描述':'彻底解决单描述',
        '问题信息_问题发生时间':'2026-06-05','问题信息_问题领域':'软件','问题信息_产品类型':'PLC软件'},'cs.xlsx','sheet',3)
    materials.add_material(materials.group('ITR-CS'),'ITR20260605085CS',{
        '问题信息_彻底解决单号':'ITR20260605085CS','问题信息_问题描述':'硬件器件损坏',
        '问题信息_问题发生时间':'2026-06-06','问题信息_问题领域':'硬件','问题信息_产品类型':'控制器硬件'},'cs.xlsx','sheet',4)
    page=client.get('/quality-scenarios/generate?preview=1&source=cs_itr&product_code=PLC&start_month=6&end_month=6')
    assert page.status_code==200
    assert '彻底解决单 + ITR（推荐）' in page.text
    assert '/materials/cs/MAT-' in page.text and '无漏测分析，基于彻底解决单' in page.text
    assert '软件（默认）' in page.text and '去重后 1 个问题' in page.text
    assert '彻底解决单描述' in page.text and '硬件器件损坏' not in page.text
    hardware=client.get('/quality-scenarios/generate?preview=1&source=cs_itr&product_code=PLC&problem_domain=HARDWARE&start_month=6&end_month=6')
    assert '去重后 1 个问题' in hardware.text and '硬件器件损坏' in hardware.text and '彻底解决单描述' not in hardware.text
    product=client.get('/quality-scenarios/generate?preview=1&source=cs_itr&product_code=PLC&source_product=PLC%E8%BD%AF%E4%BB%B6&start_month=6&end_month=6')
    assert '去重后 1 个问题' in product.text and 'PLC软件' in product.text


def test_cs_itr_portrait_period_is_not_erased_by_kpi_enrichment(tmp_path):
    db=tmp_path/'portrait-time.db';app=create_app(db);materials=MaterialRepository(db)
    mid,_=materials.add_material(materials.group('ITR-CS'),'ITR20260605084CS',{
        '问题信息_彻底解决单号':'ITR20260605084CS','问题信息_问题描述':'现场状态异常',
        '问题信息_问题发生时间':'2026-06-05','问题信息_客户行业':'锂电'},'cs.xlsx','sheet',3)
    service=app.state.scenario_generation_service
    records=material_scene_records(service)
    service.save_source_snapshot('PORTRAIT-TIME',records)
    sid=service.scenarios.save_scenario('',{'scenario_code':'TIME','name':'现场状态一致性','product_code':'PLC',
        'activity_code':'STATE_DATA_PROCESSING','status':'IN_REVIEW'}, {})
    with service.scenarios.connect() as c:
        c.execute('INSERT INTO quality_scenario_evidence VALUES(?,?,?)',(sid,mid,'{}'))
    facts=ScenarioAssets(service.scenarios).facts()
    assert facts[mid]['year']=='2026' and facts[mid]['month']=='6'
    assert facts[mid]['period_source']=='彻底解决单/ITR事实时间'


def test_itr_only_candidate_is_enhanced_in_place_when_cs_arrives(tmp_path):
    db=tmp_path/'late-cs.db';scenes=ScenarioRepository(db);materials=MaterialRepository(db);client=CountingClient()
    itr,_=materials.add_material(materials.group('ITR'),'ITR20260605086',{
        '问题信息_ITR单号':'ITR20260605086','问题信息_问题描述':'设备连续运行后状态异常'},'itr.xlsx','sheet',2)
    service=ScenarioGenerationService(NoAnalyses(),scenes,tmp_path,client)
    first=service.generate('PLC','1','12',selected_ids=[itr])
    original_id=first['scenario_ids'][0]
    cs,_=materials.add_material(materials.group('ITR-CS'),'ITR20260605086CS',{
        '问题信息_彻底解决单号':'ITR20260605086CS','问题信息_问题描述':'连续运行后资源未释放',
        '技术根因分析与纠正_TRC根因':'缓存未释放','技术根因分析与纠正_TRC纠正信息':'修复资源释放'},'cs.xlsx','sheet',2)
    second=service.generate('PLC','1','12',selected_ids=[cs])
    assert second['updated_count']==1 and second['scenario_ids']==[original_id]
    assert len(scenes.scenarios())==1 and scenes.scenario(original_id)['version_no']==2
    assert {x['knowledge_id'] for x in scenes.scenario(original_id)['evidence']}=={itr,cs}
    assert scenes.issue_classifications(second['generation_id'])[0]['status']=='UPDATED'


def test_selected_groups_prevent_cross_group_arbitrary_choice(tmp_path):
    db=tmp_path/'groups.db';scenes=ScenarioRepository(db);materials=MaterialRepository(db)
    base=materials.group('ITR-CS')
    materials.add_material(base,'ITR20260605087CS',{'问题信息_问题描述':'标准组记录'},'base.xlsx','sheet',2)
    with materials.connect() as c:
        c.execute("INSERT INTO data_group(group_id,group_code,group_name,material_type) VALUES('DG-CS-LOW','CS-LOW','低质量批量组','ITR_CS')")
    custom=materials.group('CS-LOW')
    materials.add_material(custom,'ITR20260605087CS',{'问题信息_问题描述':'低质量组记录'},'low.xlsx','sheet',2)
    service=ScenarioGenerationService(NoAnalyses(),scenes,tmp_path,CountingClient())
    assert material_scene_records(service)[0]['source_status']=='CONFLICT'
    selected=material_scene_records(service,{'group_ids':[base['group_id']]})
    assert len(selected)==1 and selected[0]['description']=='标准组记录'
