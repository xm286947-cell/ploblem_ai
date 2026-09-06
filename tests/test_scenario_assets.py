import pytest
from fastapi.testclient import TestClient
from quality_knowledge.web.app import create_app
from quality_knowledge.scenario_assets import ScenarioAssets


def setup_assets(tmp_path):
    app=create_app(tmp_path/'assets.db');repo=app.state.scenario_repository;service=ScenarioAssets(repo)
    a=repo.save_scenario('',{'scenario_code':'A','name':'在线监控流畅性','product_code':'PLC','activity_code':'ONLINE_MONITORING','status':'PUBLISHED'}, {})
    b=repo.save_scenario('',{'scenario_code':'B','name':'在线监控流畅性','product_code':'PLC','activity_code':'ONLINE_MONITORING','status':'IN_REVIEW'}, {})
    with repo.connect() as c:
        for sid,kid in ((a,'I1'),(b,'I1'),(b,'I2')):c.execute('INSERT INTO quality_scenario_evidence VALUES(?,?,?)',(sid,kid,'{}'))
    service.facts=lambda:{'I1':{'business_issue_id':'ITR202501001CS','industry':'锂电','customer':'甲','product':'P1','year':'2026','month':'2月'},'I2':{'business_issue_id':'ITR202602002','industry':'3C','customer':'乙','product':'P2','year':'2026','month':'8月'}}
    return app,repo,service,a,b


def test_grouping_preserves_evidence_and_distinct_industry(tmp_path):
    app,repo,s,a,b=setup_assets(tmp_path)
    s.group(a,[b]);r=s.report()
    assert len(r['assets'])==1 and r['issue_count']==2
    assert repo.scenario(b)['evidence']
    assert s.report({'industry':'锂电'})['issue_count']==1
    assert s.report({'industry':'3C','customer':'甲'})['issue_count']==0
    assert {x['label'] for x in r['distributions']['period']}=={'2026 Q1','2026 Q3'}
    assert {x['label'] for x in s.report({'grain':'half'})['distributions']['period']}=={'2026 H1','2026 H2'}
    with pytest.raises(ValueError):s.group(b,[a])
    s.ungroup(a,[b]);assert len(s.catalog())==2


def test_metric_chain_and_context_preserved(tmp_path):
    app,repo,s,a,b=setup_assets(tmp_path)
    s.save_context(a,{'scale_value':'128','scale_unit':'轴','environment':'持续运行'})
    with pytest.raises(ValueError):s.add_metric(a,{'name':'响应时间'})
    s.add_metric(a,{'name':'响应时间P95','concern':'流畅性','observable':'操作延迟','method':'记录响应时长取95分位数','data_source':'测试日志','statistical_scope':'128轴持续运行2小时','test_spec':'固定负载重复测量'})
    assert s.report()['metric_count']==1
    assert s.report({'environment':'持续运行'})['issue_count']==1
    client=TestClient(app)
    assert client.get('/quality-scenario-assets').status_code==200
    page=client.get('/quality-scenario-assets/'+a)
    assert page.status_code==200 and '响应时间P95' in page.text and '人工归集' in page.text


def test_unknown_period_not_guessed_from_itr_and_no_capability_loss(tmp_path):
    app,repo,s,a,b=setup_assets(tmp_path)
    s.facts=lambda:{'I1':{'business_issue_id':'ITR20260801001'}}
    assert s.report()['unknown_time_count']==2
    repo.save_scenario_capability_gap(a,{'capability_axis':'TEST','capability_code':'TEST_METHOD','gap_description':'边界验证不足'})
    repo.save_industry_variants(a,[])
    assert len(repo.scenario(a)['capability_gaps'])==1


def test_http_forms_and_matrix_routes(tmp_path):
    app,repo,s,a,b=setup_assets(tmp_path);client=TestClient(app)
    response=client.post(f'/quality-scenario-assets/{a}/context',data={'environment':'高负载','scale_value':'64','scale_unit':'轴'})
    assert response.status_code==200 and '高负载' in response.text
    response=client.post(f'/quality-scenario-assets/{a}/group',data={'member_ids':b})
    assert response.status_code==200 and len(s.catalog())==1
    assert client.get('/quality-scenario-assets?x=period&y=scenario&grain=year').status_code==200
    portrait=client.get('/quality-scenario-assets/portrait')
    assert portrait.status_code==200 and '客户 / 行业质量场景画像' in portrait.text
    assert '不虚构系统拓扑' in portrait.text
    assert '基于已有场景生成画像解读' in portrait.text and '从市场问题补充画像' in portrait.text
    assert client.get('/quality-scenario-assets?x=invalid').status_code==400
    response=client.post(f'/quality-scenario-assets/{a}/ungroup',data={'member_ids':b})
    assert response.status_code==200 and len(s.catalog())==2


def test_delete_root_restores_members_and_cleans_extensions(tmp_path):
    app,repo,s,a,b=setup_assets(tmp_path)
    s.group(a,[b]);s.save_context(a,{'environment':'测试环境'})
    repo.delete_scenario(a)
    assert [x['scenario_id'] for x in s.catalog()]==[b]
    with repo.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM scenario_asset_context').fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM scenario_asset_member').fetchone()[0]==0
    with pytest.raises(ValueError):s.report({'grain':'invalid'})


def test_customer_portrait_keeps_products_separate_and_counts_unique_issues(tmp_path):
    app,repo,s,a,b=setup_assets(tmp_path)
    report=s.portrait()
    assert [(x['label'],x['issue_count']) for x in report['product_portrait']]==[('P1',1),('P2',1)]
    assert report['issue_count']==2
