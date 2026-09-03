from fastapi.testclient import TestClient

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
