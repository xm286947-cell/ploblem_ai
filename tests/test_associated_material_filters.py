from fastapi.testclient import TestClient

from quality_knowledge.web.app import create_app


def seed(app):
    repo=app.state.material_repository;group=repo.group('SW-OPS')
    rows=[
        ('ITR20260100001CS','行业甲','客户甲','业务甲','SPDT甲','型号甲','系列甲'),
        ('ITR20260100002CS','行业甲','客户乙','业务甲','SPDT乙','型号乙','系列甲'),
        ('ITR20260100003CS','行业乙','客户丙','业务乙','SPDT丙','型号丙','系列乙'),
    ]
    for number,industry,customer,ipmt,spdt,model,series in rows:
        repo.add_material(group,number,{'问题信息_彻底解决单号':number,'问题信息_客户行业':industry,
            '问题信息_客户名称':customer,'问题信息_IPMT':ipmt,'问题信息_SPDT':spdt,
            '问题信息_产品型号':model,'问题信息_产品系列':series,
            '数据运营_KPI计入月份':'2026-01','问题信息_问题描述':number},'scope.xlsx','Sheet1',1)
    return repo


def test_software_workbench_filters_are_cascading(tmp_path):
    app=create_app(tmp_path/'app.db');repo=seed(app)
    result=repo.search_materials('SW-OPS',industry='行业甲')
    assert result['total']==2
    assert {x['label'] for x in result['options']['customer']}=={'客户甲','客户乙'}
    assert '客户丙' not in {x['label'] for x in result['options']['customer']}
    result=repo.search_materials('SW-OPS',industry='行业甲',customer='客户甲')
    assert result['total']==1
    assert [x['label'] for x in result['options']['product_model']]==['型号甲']
    page=TestClient(app).get('/materials/software-operations?industry=行业甲&customer=客户甲')
    assert page.status_code==200 and '共 1 条' in page.text
    assert '产品系列' in page.text and '关键行业 / 客户分布' in page.text


def test_distribution_and_candidate_use_same_associated_scope(tmp_path):
    app=create_app(tmp_path/'app.db');seed(app);client=TestClient(app)
    page=client.get('/software-operation-distribution?ipmt=业务甲&product_series=系列甲')
    assert page.status_code==200 and '关键行业 / 客户分布' in page.text
    assert '行业甲' in page.text and '行业乙' not in page.text
    candidate=client.get('/quality-scenarios/generate?preview=1&industry=行业甲&customer=客户甲&ipmt=业务甲')
    assert candidate.status_code==200 and '当前筛选范围 1 个问题' in candidate.text
    assert '客户丙' not in candidate.text
