from copy import deepcopy
from quality_knowledge.scenario_decisions import decision_digest
from quality_knowledge.web.app import create_app
from quality_knowledge.scenario_assets import ScenarioAssets
from fastapi.testclient import TestClient


class Taxonomy:
    def taxonomy(self,product_code):
        return {'activities':[{'activity_code':'MONITOR','label_zh':product_code+'在线监控'}]}


def sample():
    row={'business':'PLC','activity':'MONITOR','issue_key':'ITR1','issue_id':'K1','asset_id':'S1','concern':'状态显示错误，客户无法判断当前值',
         'industry':'锂电','customer':'甲','product':'P1','scale':'未知规模','environment':'未知工况','period':'未知时间'}
    return {'records':[row,dict(row),{**row,'issue_key':'ITR2','issue_id':'K2','industry':'3C','customer':'乙'}],
            'issue_count':2,'unknown_time_count':2,'assets':[{'status':'IN_REVIEW','metrics':[]}]}


def test_themes_are_deduplicated_traceable_and_not_merged():
    report=sample();before=deepcopy(report);d=decision_digest(report,Taxonomy())
    trust=next(t for t in d['themes'] if t['theme']=='状态与结果不可信')
    assert trust['count']==2 and len(trust['issues'])==2
    assert trust['industries']==['3C','锂电'] and trust['activity']=='PLC在线监控'
    assert all(e['matched_text'] and e['concern'] for e in trust['issues'])
    assert d['unknown_time']==2 and d['no_metrics']==1 and d['unmatched']==0
    assert report==before


def test_product_boundaries_negation_and_unmatched():
    report=sample()
    report['records']=[report['records'][0],{**report['records'][0],'business':'HMI','issue_key':'ITR3'}, {**report['records'][0],'issue_key':'ITR4','concern':'没有卡顿'}]
    report['issue_count']=3
    d=decision_digest(report,Taxonomy())
    assert all(t['count']==1 for t in d['themes'])
    assert d['unmatched']==1
    assert not any(c['label']=='响应迟缓或交互不流畅' for c in d['concerns'])


def test_both_pages_render_action_evidence_and_empty_state(tmp_path):
    app=create_app(tmp_path/'decision.db');repo=app.state.scenario_repository
    sid=repo.save_scenario('',{'scenario_code':'DEC','name':'测试场景','product_code':'PLC','activity_code':'ONLINE_MONITORING','concern_points':'多设备状态显示错误','status':'IN_REVIEW'}, {})
    with repo.connect() as c:c.execute('INSERT INTO quality_scenario_evidence VALUES(?,?,?)',(sid,'SYNTHETIC-1','{}'))
    client=TestClient(app)
    for url in ('/quality-scenario-assets','/quality-scenarios/insights'):
        response=client.get(url)
        assert response.status_code==200
        assert '当前能得出的结论与下一步' in response.text and '单例线索' in response.text
        assert '研发建议' in response.text and '测试建议' in response.text and '规则命中' in response.text
        assert 'SYNTHETIC-1' in response.text
    page=client.get('/quality-scenario-assets?industry=不存在')
    assert '当前没有可支持主题归纳的关注点' in page.text
    assert not ScenarioAssets(repo).catalog()[0]['metrics']
