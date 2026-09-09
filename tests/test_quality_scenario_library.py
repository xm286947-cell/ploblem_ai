import json

from fastapi.testclient import TestClient
from openpyxl import Workbook

from quality_knowledge.scenarios import ScenarioRepository
from quality_knowledge.web.app import create_app


def test_default_plc_taxonomy_and_independent_pages(tmp_path):
    client=TestClient(create_app(tmp_path/"app.db"))
    repository=client.app.state.scenario_repository
    taxonomy=repository.taxonomy()
    assert taxonomy["status"]=="ACTIVE"
    assert len(taxonomy["lifecycles"])==6
    assert len(taxonomy["activities"])==35
    power_loss=next(x for x in taxonomy['activities'] if x['activity_code']=='POWER_LOSS_RETENTION_RECOVERY')
    assert power_loss['lifecycle_code']=='RUNTIME_EXECUTION' and power_loss['label_zh']=='掉电数据保持与上电恢复'
    assert '掉电 → 数据保持 → 重新上电' in power_loss['chain_text'] and '掉电不丢关键数据' in power_loss['description']
    page=client.get("/quality-scenarios")
    assert page.status_code==200 and "质量场景库" in page.text and "全部IPMT" in page.text and "全部SPDT" in page.text and "全部产品型号" in page.text
    settings=client.get("/settings/scenario-taxonomy")
    assert settings.status_code==200 and "场景词典配置" in settings.text and "基于当前版本创建草稿" in settings.text


def test_product_taxonomy_is_independent_and_can_copy_plc(tmp_path):
    repository=ScenarioRepository(tmp_path/'product-taxonomy.db')
    assert repository.taxonomy_active('HMI') is None
    draft_id=repository.create_draft('HMI','PLC')
    draft=repository.taxonomy(draft_id)
    assert draft['product_code']=='HMI' and len(draft['activities'])==len(repository.taxonomy_active('PLC')['activities'])
    repository.save_lifecycle(draft_id,'SOFTWARE_DEBUGGING','HMI软件调试','HMI画面、通信和交互调试价值',True)
    repository.activate(draft_id)
    assert repository.taxonomy_active('HMI')['lifecycles'][1]['description']=='HMI画面、通信和交互调试价值'
    assert repository.taxonomy_active('PLC')['lifecycles'][1]['description']!='HMI画面、通信和交互调试价值'


def test_scenario_listing_resolves_labels_from_its_product_taxonomy_version(tmp_path):
    client=TestClient(create_app(tmp_path/'product-labels.db'));repository=client.app.state.scenario_repository
    draft_id=repository.create_draft('HMI','PLC')
    repository.save_lifecycle(draft_id,'HMI_OPERATION','HMI运行操作','面向HMI运行期的人机操作',True)
    repository.save_activity(draft_id,'HMI_OPERATION','HMI_ALARM_CONFIRM','报警查看与确认','报警出现 → 查看 → 确认 → 处置','处理设备报警',True)
    repository.activate(draft_id)
    repository.save_scenario('',{
        'scenario_code':'HMI-AI-1','name':'HMI报警确认可靠性','product_code':'HMI',
        'taxonomy_version_id':draft_id,'lifecycle_code':'HMI_OPERATION',
        'activity_code':'HMI_ALARM_CONFIRM','status':'IN_REVIEW',
    },{})
    page=client.get('/quality-scenarios')
    assert page.status_code==200
    assert 'HMI运行操作' in page.text and '报警查看与确认' in page.text
    assert '>HMI_OPERATION<' not in page.text and '>HMI_ALARM_CONFIRM<' not in page.text


def test_product_taxonomy_page_shows_missing_state_and_copy_entry(tmp_path):
    client=TestClient(create_app(tmp_path/'product-page.db'))
    page=client.get('/settings/scenario-taxonomy?product_code=HMI')
    assert page.status_code==200 and 'HMI 尚未配置场景词典' in page.text and '复制 PLC' in page.text


def test_taxonomy_changes_use_new_draft_version(tmp_path):
    repository=ScenarioRepository(tmp_path/"taxonomy.db")
    active=repository.taxonomy()
    draft_id=repository.create_draft()
    assert draft_id!=active["version_id"]
    repository.save_lifecycle(draft_id,"SOFTWARE_DEBUGGING","软件联调","修改后的定义",True)
    draft=repository.taxonomy()
    assert draft["status"]=="DRAFT"
    assert next(x for x in draft["lifecycles"] if x["lifecycle_code"]=="SOFTWARE_DEBUGGING")["label_zh"]=="软件联调"
    original=repository.taxonomy(active["version_id"])
    assert next(x for x in original["lifecycles"] if x["lifecycle_code"]=="SOFTWARE_DEBUGGING")["label_zh"]=="软件调试"
    repository.activate(draft_id)
    assert next(x for x in repository.versions() if x["version_id"]==draft_id)["status"]=="ACTIVE"


def test_existing_taxonomy_gets_power_loss_activity_without_reinitialization(tmp_path):
    db=tmp_path/'taxonomy-upgrade.db';repository=ScenarioRepository(db);version_id=repository.taxonomy_active()['version_id']
    with repository.connect() as c:c.execute("DELETE FROM scenario_activity WHERE version_id=? AND activity_code='POWER_LOSS_RETENTION_RECOVERY'",(version_id,))
    upgraded=ScenarioRepository(db).taxonomy_active()
    assert any(x['activity_code']=='POWER_LOSS_RETENTION_RECOVERY' for x in upgraded['activities'])


def test_scenario_scope_filters_ipmt_spdt_and_product_model(tmp_path):
    client=TestClient(create_app(tmp_path/"scenario.db"))
    response=client.post("/quality-scenarios/save",data={
        "scenario_id":"","scenario_code":"PLC-DEBUG-001","name":"大型PLC工程在线监控流畅性",
        "lifecycle_code":"SOFTWARE_DEBUGGING","activity_code":"ONLINE_MONITORING",
        "experience_requirement":"持续流畅","concern_points":"卡顿","quality_attribute":"性能效率","quality_subcharacteristic":"时间特性",
        "applicable_boundary":"大型工程","validation_direction":"性能回归","measurement_suggestion":"记录响应时间并计算P95","status":"PUBLISHED",
        "ipmt":["控制产品IPMT"],"spdt":["PLC SPDT"],"product_model":["AM600"],"industry":["锂电"],"customer_name":["示例客户"],"customer_level":["A级"],"customer_status":["复位可恢复"],"occurrence_phase":["现场调试"],
    },follow_redirects=False)
    assert response.status_code==303
    matched=client.get("/quality-scenarios?ipmt=控制产品IPMT&spdt=PLC%20SPDT&product_model=AM600")
    assert matched.status_code==200 and "大型PLC工程在线监控流畅性" in matched.text
    assert "下载运行 → 在线监控 → 变量观察" in matched.text and "时间特性" in matched.text and "计算P95" in matched.text
    assert "锂电" in matched.text and "示例客户" in matched.text and "复位可恢复" in matched.text and "现场调试" in matched.text
    detail=client.get(response.headers['location'])
    assert '场景链路（由业务活动配置决定）' in detail.text and 'name="scenario_chain"' not in detail.text
    assert 'name="quality_subcharacteristic"' in detail.text and 'name="measurement_suggestion"' in detail.text and '原始问题发生阶段（仅参考）' in detail.text
    assert "大型PLC工程在线监控流畅性" in client.get("/quality-scenarios?industry=锂电&customer_name=示例客户").text
    missing=client.get("/quality-scenarios?product_model=H3U")
    assert "大型PLC工程在线监控流畅性" not in missing.text
    assert "场景资产" in matched.text


def test_new_scenario_route_is_not_swallowed_by_dynamic_detail(tmp_path):
    client=TestClient(create_app(tmp_path/"route.db"))
    page=client.get("/quality-scenarios/new")
    assert page.status_code==200 and "新建质量场景" in page.text


def test_scenario_can_be_deleted_from_listing(tmp_path):
    client=TestClient(create_app(tmp_path/'delete.db'));repository=client.app.state.scenario_repository
    scenario_id=repository.save_scenario('',{'scenario_code':'DELETE-1','name':'待删除场景','activity_code':'ONLINE_MONITORING','status':'DRAFT'}, {})
    page=client.get('/quality-scenarios')
    assert f'/quality-scenarios/{scenario_id}/delete' in page.text
    response=client.post(f'/quality-scenarios/{scenario_id}/delete',follow_redirects=False)
    assert response.status_code==303 and repository.scenario(scenario_id) is None


def test_bulk_delete_only_removes_unconfirmed_ai_scenarios_in_filter_scope(tmp_path):
    client=TestClient(create_app(tmp_path/'bulk-delete.db'));repository=client.app.state.scenario_repository
    for generation_id in ('QSG-A','QSG-B'):
        repository.create_generation(generation_id,'PLC','1月','12月',1,'TEST')
        repository.update_generation(generation_id,status='COMPLETED')
    candidate=repository.save_generated_candidate('AI-DELETE',{'name':'待清理AI场景','activity_code':'ONLINE_MONITORING','evidence_issue_ids':['QK-A']},{'INDUSTRY':['锂电']},'QSG-A','PLC','1月','12月','model-a')
    published=repository.save_generated_candidate('AI-PUBLISHED',{'name':'已发布AI场景','activity_code':'ONLINE_MONITORING','status':'PUBLISHED','quality_classification_status':'CONFIRMED','evidence_issue_ids':['QK-B']},{'INDUSTRY':['锂电']},'QSG-B','PLC','1月','12月','model-a')
    repository.save_scenario(published,{'scenario_code':'AI-PUBLISHED','name':'已发布AI场景','status':'PUBLISHED','quality_classification_status':'CONFIRMED','activity_code':'ONLINE_MONITORING'},{'INDUSTRY':['锂电']})
    manual=repository.save_scenario('',{'scenario_code':'MANUAL-1','name':'人工场景','status':'DRAFT','activity_code':'ONLINE_MONITORING'},{'INDUSTRY':['锂电']})
    outside=repository.save_generated_candidate('AI-OUTSIDE',{'name':'其他行业AI场景','activity_code':'ONLINE_MONITORING','evidence_issue_ids':['QK-C']},{'INDUSTRY':['光伏']},'QSG-A','PLC','1月','12月','model-a')
    page=client.get('/quality-scenarios?industry=锂电')
    assert '/quality-scenarios/delete-generated' in page.text and '一键清理当前范围' in page.text
    response=client.post('/quality-scenarios/delete-generated',data={'industry':'锂电'},follow_redirects=False)
    assert response.status_code==303 and 'deleted=1' in response.headers['location'] and 'protected=2' in response.headers['location']
    assert repository.scenario(candidate) is None
    assert repository.scenario(published) and repository.scenario(manual) and repository.scenario(outside)
    assert repository.issue_classifications('QSG-A')==[]


def test_generation_records_can_be_deleted_without_deleting_scenarios(tmp_path):
    db=tmp_path/'generation-delete.db';client=TestClient(create_app(db));repository=client.app.state.scenario_repository
    repository.create_generation('QSG-DONE','PLC','1月','12月',1,'TEST')
    scenario_id=repository.save_generated_candidate('AI-KEEP',{'name':'保留场景','activity_code':'ONLINE_MONITORING','evidence_issue_ids':['QK-1']},{},'QSG-DONE','PLC','1月','12月','model-a')
    repository.initialize_issue_classifications('QSG-DONE',[{'knowledge_id':'QK-1','business_issue_id':'ITR-1'}])
    repository.update_generation('QSG-DONE',status='COMPLETED',candidate_count=1)
    response=client.post('/quality-scenarios/generations/QSG-DONE/delete',follow_redirects=False)
    assert response.status_code==303 and repository.generation('QSG-DONE') is None
    assert repository.scenario(scenario_id) is not None
    assert repository.issue_classifications('QSG-DONE')==[]
    restarted=ScenarioRepository(db)
    with restarted.connect() as c:
        assert c.execute("SELECT 1 FROM quality_scenario_generation_candidate WHERE generation_id='QSG-DONE'").fetchone() is None
        summary=c.execute("SELECT evidence_summary FROM quality_scenario_evidence WHERE scenario_id=?",(scenario_id,)).fetchone()[0]
    assert 'generation_id' not in json.loads(summary)
    page=client.get('/quality-scenarios/generate')
    assert '清空已结束记录' not in page.text


def test_running_generation_record_is_protected_from_deletion(tmp_path):
    client=TestClient(create_app(tmp_path/'running-delete.db'));repository=client.app.state.scenario_repository
    repository.create_generation('QSG-RUN','PLC','1月','12月',1,'TEST')
    repository.update_generation('QSG-RUN',status='RUNNING')
    response=client.post('/quality-scenarios/generations/QSG-RUN/delete',follow_redirects=False)
    assert response.status_code==409 and repository.generation('QSG-RUN') is not None
    assert '删除记录' not in client.get('/quality-scenarios/generate').text


def test_finished_generation_records_can_be_cleared_together(tmp_path):
    client=TestClient(create_app(tmp_path/'clear-generations.db'));repository=client.app.state.scenario_repository
    for generation_id,status in [('QSG-DONE','COMPLETED'),('QSG-FAIL','FAILED'),('QSG-RUN','RUNNING')]:
        repository.create_generation(generation_id,'PLC','1月','12月',1,'TEST');repository.update_generation(generation_id,status=status)
    response=client.post('/quality-scenarios/generations/delete-finished',follow_redirects=False)
    assert response.status_code==303 and 'records_deleted=2' in response.headers['location']
    assert repository.generation('QSG-DONE') is None and repository.generation('QSG-FAIL') is None
    assert repository.generation('QSG-RUN') is not None


def test_taxonomy_template_import_preserves_definition_participants_and_goal(tmp_path):
    source=tmp_path/'taxonomy.xlsx';book=Workbook();life=book.active;life.title='使用生命周期'
    life.append(['标题']);life.append(['说明']);life.append(['使用生命周期阶段','阶段价值','阶段描述','阶段目标'])
    life.append(['软件调试','把系统调通','在线验证和调整控制功能','局部功能正确工作'])
    activity=book.create_sheet('业务活动场景_重构');activity.append(['标题']);activity.append(['说明']);activity.append(['阶段','业务活动场景','场景链路','参与对象','价值与描述','目标'])
    activity.append(['软件调试','运动控制调试','轴上线 → 点动 → 定位 → 验证','iFA / PLC / 伺服','验证运动控制链路','运动准确且稳定'])
    book.save(source)
    repository=ScenarioRepository(tmp_path/'import.db');draft=repository.create_draft()
    result=repository.import_taxonomy_workbook(draft,source)
    taxonomy=repository.taxonomy(draft);life_row=next(x for x in taxonomy['lifecycles'] if x['label_zh']=='软件调试');row=next(x for x in taxonomy['activities'] if x['label_zh']=='运动控制调试')
    assert result=={'lifecycle_count':1,'activity_count':1}
    assert life_row['value_statement']=='把系统调通' and life_row['objective']=='局部功能正确工作'
    assert row['description']=='验证运动控制链路' and row['participating_systems']=='iFA / PLC / 伺服' and row['objective']=='运动准确且稳定'


def test_taxonomy_activities_can_be_saved_in_one_request_and_returns_to_section(tmp_path):
    client=TestClient(create_app(tmp_path/'bulk.db'));repository=client.app.state.scenario_repository;draft=repository.create_draft()
    response=client.post('/settings/scenario-taxonomy/activities/bulk',data={
        'version_id':draft,'activity_code':['ONLINE_MONITORING','POWER_LOSS_RETENTION_RECOVERY'],
        'lifecycle_code':['SOFTWARE_DEBUGGING','RUNTIME_EXECUTION'],'label_zh':['在线监控与调试','掉电恢复'],
        'chain_text':['连接 → 监控 → 分析','运行 → 掉电 → 上电 → 恢复'],'participating_systems':['iFA / PLC','PLC / 存储'],
        'description':['在线观察与诊断','保持关键数据并恢复'],'objective':['调试过程流畅','数据正确恢复'],'enabled_code':['ONLINE_MONITORING','POWER_LOSS_RETENTION_RECOVERY'],
    },follow_redirects=False)
    assert response.status_code==303 and response.headers['location'].endswith('#business-activities')
    rows={x['activity_code']:x for x in repository.taxonomy(draft)['activities']}
    assert rows['ONLINE_MONITORING']['description']=='在线观察与诊断'
    assert rows['POWER_LOSS_RETENTION_RECOVERY']['participating_systems']=='PLC / 存储'
    page=client.get(f'/settings/scenario-taxonomy?product_code=PLC')
    assert '保存全部业务活动' in page.text and '从 Excel 模板导入' in page.text and '定义/价值描述' in page.text


def test_quality_models_are_versioned_and_scenario_uses_standard_codes(tmp_path):
    client=TestClient(create_app(tmp_path/'quality-model.db'));repository=client.app.state.scenario_repository
    models=repository.quality_models()
    assert models['versions']['ISO_IEC_25010_2023_PRODUCT']['standard_ref']=='ISO/IEC 25010:2023'
    assert len(models['product_characteristics'])==9
    assert {x['label_zh'] for x in models['quality_in_use']}=={'有效性','效率','满意度','免除风险','情境覆盖'}
    response=client.post('/quality-scenarios/save',data={
        'scenario_code':'STD-1','name':'在线监控流畅性','status':'IN_REVIEW','activity_code':'ONLINE_MONITORING',
        'customer_perception':'页面卡顿、响应慢','primary_experience_code':'EFFICIENT_SMOOTH',
        'quality_in_use_codes':['EFFICIENCY','SATISFACTION'],'primary_quality_characteristic_code':'PERFORMANCE_EFFICIENCY',
        'quality_subcharacteristic_codes':['TIME_BEHAVIOUR','RESOURCE_UTILIZATION'],'quality_classification_status':'CONFIRMED',
    },follow_redirects=False)
    item=repository.scenario(response.headers['location'].rsplit('/',1)[-1])
    assert item['primary_experience_code']=='EFFICIENT_SMOOTH' and item['quality_in_use_codes']==['EFFICIENCY','SATISFACTION']
    assert item['quality_attribute']=='性能效率' and item['quality_subcharacteristic']=='时间特性、资源利用率'
    page=client.get(response.headers['location'])
    assert 'ISO/IEC 25010:2023' in page.text and '主要客户质量体验' in page.text and '使用质量要素' in page.text
