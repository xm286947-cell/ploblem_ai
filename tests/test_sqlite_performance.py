import hashlib
import json

from quality_knowledge.materials import MaterialRepository
from quality_knowledge.scenarios import ScenarioRepository


def test_material_search_filters_and_pages_in_sql(tmp_path, monkeypatch):
    repo=MaterialRepository(tmp_path/'materials.db')
    rows=[]
    for index in range(240):
        raw={'问题信息_问题描述':f'问题 {index}','问题信息_产品型号':'PLC' if index%2 else 'HMI',
             '问题信息_问题领域':'软件','数据运营_KPI计入月份':f'{index%12+1}月'}
        text=json.dumps(raw,ensure_ascii=False)
        rows.append((f'M{index}','DG-SW-OPS','SOFTWARE_OPERATION',f'ITR2026{index:08d}CS',f'ITR2026{index:08d}',1,hashlib.sha256(text.encode()).hexdigest(),text))
    with repo.connect() as c:
        c.executemany('INSERT INTO source_material(material_id,group_id,material_type,business_key,canonical_itr,version_no,source_hash,raw_json) VALUES(?,?,?,?,?,?,?,?)',rows)
    monkeypatch.setattr(repo,'list_materials',lambda *args,**kwargs:(_ for _ in ()).throw(AssertionError('不应整库读取')))
    result=repo.search_materials('SW-OPS',q='PLC',domain='软件',month='2月',year='2026',page=2,page_size=5)
    assert result['total']==20 and len(result['items'])==5 and result['pages']==4
    assert result['months'][0] in {'9月','8月'} and result['years']==['2026']


def test_connection_tuning_and_precise_scenario_lookup(tmp_path):
    repo=ScenarioRepository(tmp_path/'scenarios.db')
    with repo.connect() as c:
        assert c.execute('PRAGMA busy_timeout').fetchone()[0]==10000
        assert c.execute('PRAGMA cache_size').fetchone()[0]==-32768
        rows=[(f'S{i}',f'C{i}',f'场景{i}','PUBLISHED',1) for i in range(120)]
        c.executemany('INSERT INTO quality_scenario(scenario_id,scenario_code,name,status,version_no) VALUES(?,?,?,?,?)',rows)
        c.executemany("INSERT INTO quality_scenario_scope VALUES(?,'IPMT',?)",[(f'S{i}',f'I{i%3}') for i in range(120)])
    item=repo.scenario('S77')
    assert item['scenario_id']=='S77' and item['scopes']['IPMT']==['I2']
    filtered=repo.scenarios(ipmt='I1',status='PUBLISHED')
    assert len(filtered)==40 and all(x['scopes']['IPMT']==['I1'] for x in filtered)
