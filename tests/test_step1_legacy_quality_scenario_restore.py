from fastapi.testclient import TestClient

from quality_knowledge.web import create_app


def _seed(repo):
    first = repo.save_scenario(
        '',
        {
            'scenario_code': 'LEGACY-RESTORE-001',
            'name': '成熟质量场景A',
            'product_code': 'PLC',
            'status': 'PUBLISHED',
            'lifecycle_code': 'OPERATIONS',
            'activity_code': 'ONLINE_MONITORING',
            'scenario_chain': '工程配置 -> 在线监控 -> 异常处置',
            'concern_points': '稳定性 / 可恢复性',
            'quality_attribute': '可靠性',
            'measurement_suggestion': '持续运行与恢复时间',
        },
        {
            'INDUSTRY': ['锂电'],
            'CUSTOMER_NAME': ['客户A'],
            'PRODUCT_MODEL': ['PLC-A'],
        },
    )
    repo.save_scenario(
        '',
        {
            'scenario_code': 'LEGACY-RESTORE-002',
            'name': '其他行业场景B',
            'product_code': 'PLC',
            'status': 'PUBLISHED',
            'activity_code': 'ONLINE_MONITORING',
        },
        {'INDUSTRY': ['光伏'], 'CUSTOMER_NAME': ['客户B']},
    )
    return first


def test_step1_restores_mature_legacy_quality_scenario_pages(tmp_path):
    client = TestClient(create_app(tmp_path / 'legacy.db'))
    repo = client.app.state.scenario_repository
    scenario_id = _seed(repo)

    page = client.get('/quality-scenarios?industry=锂电&q=成熟')
    assert page.status_code == 200
    assert '质量场景库' in page.text
    assert '成熟质量场景A' in page.text
    assert '其他行业场景B' not in page.text
    assert 'AI生成候选' in page.text
    assert client.app.state.legacy_quality_scenario_restore_source == 'ce2eca157c4403b97036dd66e5218cd74fe53700'

    detail = client.get(f'/quality-scenarios/{scenario_id}')
    assert detail.status_code == 200
    assert '成熟质量场景A' in detail.text

    assets = client.get('/quality-scenario-assets')
    assert assets.status_code == 200
    assert '质量场景资产与业务洞察' in assets.text
    assert '/quality-scenario-assets/portrait' in assets.text

    portrait = client.get('/quality-scenario-assets/portrait')
    assert portrait.status_code == 200
    assert '客户 / 行业质量场景画像' in portrait.text
    assert '返回资产与洞察' in portrait.text


def test_step1_does_not_regress_old_problem_platform_entries(tmp_path):
    client = TestClient(create_app(tmp_path / 'legacy.db'))
    for path in ('/issues', '/analysis', '/import'):
        response = client.get(path)
        assert response.status_code == 200, (path, response.text[:500])
