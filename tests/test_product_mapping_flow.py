from fastapi.testclient import TestClient

from quality_knowledge.web.app import create_app


def test_new_product_has_mapping_entry_and_bootstrap_workflow(tmp_path):
    app = create_app(tmp_path / 'product-flow.db')
    client = TestClient(app)

    created = client.post('/api/settings/products', json={
        'product_code': 'ROBOT', 'product_name': '机器人',
        'product_kind': 'ROBOT', 'default_issue_domain': 'MECHANICAL',
    })
    assert created.status_code == 200

    products_page = client.get('/settings/products')
    assert '机器人' in products_page.text
    assert '/settings/mapping?business_type=ROBOT' in products_page.text

    page = client.get('/settings/mapping?business_type=ROBOT')
    assert page.status_code == 200
    assert '从公共字段库创建 ROBOT Mapping Draft' in page.text
    assert 'HMI' in page.text and '机器人' in page.text

    created_draft = client.post('/settings/mapping/ROBOT/bootstrap', follow_redirects=False)
    assert created_draft.status_code == 303
    draft = app.state.mapping_configuration_service.list_configs('ROBOT')[0]
    assert draft['status'] == 'DRAFT'
    assert any(x['canonical_field'] == 'business_issue_id' and x['required'] for x in draft['mappings'])
    # Shared catalog is materially larger than the generic fallback and carries
    # fields contributed by the shipped HMI/PLC/IFA mappings.
    assert len(draft['mappings']) > 7
    assert any(x['target_field'] == 'product_series' for x in draft['mappings'])

    # Re-entering does not create duplicate drafts.
    assert client.post('/settings/mapping/ROBOT/bootstrap', follow_redirects=False).status_code == 303
    assert len(app.state.mapping_configuration_service.list_configs('ROBOT')) == 1
