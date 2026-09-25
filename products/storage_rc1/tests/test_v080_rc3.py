from fastapi.testclient import TestClient

from storage_life import core
from storage_life.app import app


def _seed(tmp_path, monkeypatch, dtype, specs):
    monkeypatch.setattr(core, 'DATA', tmp_path)
    monkeypatch.setattr(core, 'DB', tmp_path / f'{dtype.replace(" ", "_")}.sqlite3')
    with core.connect() as con:
        con.execute("INSERT INTO sources VALUES (?,?,?,?,?,?,?,?)", ('s','x.pdf','h',str(tmp_path/'x.pdf'),'','','8','now'))
        con.execute("INSERT INTO devices VALUES (?,?,?,?,?)", ('d','Vendor','Family',dtype,'s'))
        for i, item in enumerate(specs, 1):
            canon, value, unit, condition = item
            cid=f'c{i}'
            con.execute("""INSERT INTO candidates(id,device_id,canonical_name,parameter_name,ai_value,ai_unit,
              condition,scope,source_page,source_text,confidence,extraction_method) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
              (cid,'d',canon,canon,value,unit,condition,'product family',i,f'{canon}: {value} {unit}'.strip(),.95,'agent_markdown'))
            con.execute("INSERT INTO candidate_evidence VALUES (?,?,?,?,?,?,?,?)",
                        (f'e{i}',cid,i,'Section',f'{canon}: {value} {unit}'.strip(),.95,'agent_markdown','product family'))
    core.rebuild_reviewed_specifications('d')
    return core.get_device_conclusion('d')


def test_rc3_nand_conclusion_is_result_oriented_and_evidence_backed(tmp_path, monkeypatch):
    c=_seed(tmp_path, monkeypatch, 'NAND Flash', [
        ('cell_type','SLC','',''),('pe_cycles','100K','cycles','with ECC'),('retention','10','years',''),
        ('ecc_capability','4 bits/528 bytes','',''),('program_fail','supported','',''),('erase_fail','supported','',''),
    ])
    assert '100' in c['lifetime_summary'] and '数据保持' in c['lifetime_summary']
    assert 'ECC' in c['diagnostic_summary']
    assert '软件' not in c['software_recommendation'] or c['software_recommendation']
    assert 1 <= len(c['key_specs']) <= 8
    assert all(x['priority'] in {'P0','P1'} for x in c['key_specs'])
    assert all(x['evidence_count'] >= 1 for x in c['key_specs'])


def test_rc3_emmc_conclusion_surfaces_ext_csd_health(tmp_path, monkeypatch):
    c=_seed(tmp_path, monkeypatch, 'eMMC', [
        ('default_user_area_type','MLC','',''),('enhanced_area_cell_type','pSLC','',''),
        ('life_time_a','01h','',''),('life_time_b','01h','',''),('pre_eol','01h','',''),
        ('ext_csd_health_report','supported','',''),
    ])
    assert 'MLC' in c['lifetime_summary']
    assert 'pSLC' in c['lifetime_summary']
    assert 'EXT_CSD' in c['diagnostic_summary']
    assert 'Pre-EOL' in c['diagnostic_summary']
    assert c['conclusion_status'] == 'complete'


def test_rc3_ssd_conclusion_surfaces_endurance_and_smart(tmp_path, monkeypatch):
    c=_seed(tmp_path, monkeypatch, 'SSD', [
        ('cell_type','TLC','',''),('tbw','600','TB',''),('dwpd','1','',''),
        ('smart_health','supported','',''),('percentage_used','supported','',''),('data_units_written','supported','',''),
        ('media_errors','supported','',''),
    ])
    assert '600' in c['lifetime_summary'] and 'TB' in c['lifetime_summary']
    assert 'SMART' in c['diagnostic_summary']
    assert '累计写入量' in c['diagnostic_summary']
    assert c['conclusion_status'] == 'complete'


def test_rc3_nor_conclusion_calls_out_software_write_counting_when_no_counter(tmp_path, monkeypatch):
    c=_seed(tmp_path, monkeypatch, 'NOR Flash', [
        ('pe_cycles','100K','cycles',''),('retention','20','years',''),
        ('status_register','supported','',''),('program_fail','supported','',''),('erase_fail','supported','',''),
    ])
    assert '100' in c['lifetime_summary']
    assert '状态寄存器' in c['diagnostic_summary']
    assert '擦写次数' in c['software_recommendation']
    assert c['conclusion_status'] == 'complete'


def test_rc3_conclusion_rebuilds_after_candidate_delete(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, 'SSD', [('tbw','600','TB',''),('smart_health','supported','','')])
    before=core.get_device_conclusion('d')
    assert before['conclusion_status'] == 'complete'
    cid=core.list_candidates('d')[0]['id']
    # Delete whichever candidate is first; if TBW disappears, status must no longer be complete.
    core.delete_candidate(cid)
    after=core.get_device_conclusion('d')
    assert after['conclusion_status'] != 'complete'
    assert after['missing_critical_fields']


def test_rc3_api_and_home_expose_conclusion_first_information_architecture(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, 'eMMC', [('life_time_a','01h','',''),('life_time_b','01h','',''),('pre_eol','01h','','')])
    client=TestClient(app)
    r=client.get('/api/devices/d/conclusion')
    assert r.status_code == 200
    assert 'lifetime_summary' in r.json() and 'diagnostic_summary' in r.json()
    family=client.get('/api/devices/d/family-view').json()
    assert 'conclusion' in family and 'key_specs' in family['conclusion']
    html=client.get('/').text
    for label in ('分析结论','关键规格','料号差异','证据','识别明细','写入寿命结论','诊断能力结论','软件关注'):
        assert label in html


def test_d2_conclusion_stays_draft_until_key_specs_are_human_confirmed(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, 'SSD', [
        ('tbw','600','TB',''),('smart_health','supported','','')
    ])
    draft = core.get_device_conclusion('d')
    assert draft['conclusion_status'] == 'complete'  # coverage is independent from human confirmation
    assert draft['verification_status'] == 'pending_confirmation'
    assert draft['is_formal'] is False
    assert draft['confirmation']['pending_key_fields']

    for c in core.list_candidates('d'):
        core.verify(c['id'], 'confirmed', c['ai_value'], c['ai_unit'], 'tester', c['condition'], c['scope'])
    formal = core.get_device_conclusion('d')
    assert formal['verification_status'] == 'confirmed'
    assert formal['is_formal'] is True
    assert formal['confirmation']['confirmed_spec_count'] == formal['confirmation']['reviewed_spec_count']


def test_d2_equivalent_duplicate_needs_only_one_human_confirmation(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'DATA', tmp_path)
    monkeypatch.setattr(core, 'DB', tmp_path / 'd2-duplicates.sqlite3')
    with core.connect() as con:
        con.execute("INSERT INTO sources VALUES (?,?,?,?,?,?,?,?)", ('s','x.pdf','h',str(tmp_path/'x.pdf'),'','','2','now'))
        con.execute("INSERT INTO devices VALUES (?,?,?,?,?)", ('d','Vendor','Family','SSD','s'))
        for i, page in enumerate((1,2), 1):
            cid=f'c{i}'
            con.execute("""INSERT INTO candidates(id,device_id,canonical_name,parameter_name,ai_value,ai_unit,
              condition,scope,source_page,source_text,confidence,extraction_method) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
              (cid,'d','tbw','tbw','600','TB','','product family',page,'TBW 600 TB',.95,'agent_single_pass'))
            con.execute("INSERT INTO candidate_evidence VALUES (?,?,?,?,?,?,?,?)",
                        (f'e{i}',cid,page,'','TBW 600 TB',.95,'agent_single_pass','product family'))
            con.execute("INSERT INTO candidate_evidence_provenance VALUES (?,?)", (f'e{i}','s'))
    core.rebuild_reviewed_specifications('d')
    assert core.list_reviewed_specifications('d')[0]['review_status'] == 'pending'
    core.verify('c1', 'confirmed', '600', 'TB', 'tester', '', 'product family')
    specs = core.list_reviewed_specifications('d')
    assert len(specs) == 1
    assert specs[0]['review_status'] == 'confirmed'
    assert set(specs[0]['candidate_ids']) == {'c1','c2'}
    assert specs[0]['evidence_count'] == 2


def test_d2_unresolved_review_gate_blocks_formal_conclusion(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, 'SSD', [('tbw','600','TB',''),('smart_health','supported','','')])
    for c in core.list_candidates('d'):
        core.verify(c['id'], 'confirmed', c['ai_value'], c['ai_unit'], 'tester', c['condition'], c['scope'])
    with core.connect() as con:
        con.execute("""INSERT INTO extraction_runs
          (id,device_id,source_id,extraction_mode,schema_valid,model_calls,unresolved_evidence_json,review_required,review_queue_json,created_at)
          VALUES (?,?,?,?,?,?,?,?,?,?)""",
          ('r','d','s','agent_single_pass_template',1,1,'[]',1,'[{"reason":"conflict"}]','now'))
    blocked = core.get_device_conclusion('d')
    assert blocked['verification_status'] == 'attention_required'
    assert blocked['is_formal'] is False
    with core.connect() as con:
        con.execute("INSERT INTO final_reviews VALUES (?,?,?,?,?,?,?)",
                    ('fr','d','ready_for_human_review','checked','[]','[]','later'))
    ready = core.get_device_conclusion('d')
    assert ready['verification_status'] == 'confirmed'
    assert ready['is_formal'] is True


def test_d2_reviewed_and_conclusion_evidence_preserve_source_provenance(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'DATA', tmp_path)
    monkeypatch.setattr(core, 'DB', tmp_path / 'd2-provenance.sqlite3')
    with core.connect() as con:
        con.execute("INSERT INTO sources VALUES (?,?,?,?,?,?,?,?)", ('s1','x.pdf','h',str(tmp_path/'x.pdf'),'','','1','now'))
        con.execute("INSERT INTO devices VALUES (?,?,?,?,?)", ('d','Vendor','Family','SSD','s1'))
        con.execute("""INSERT INTO candidates(id,device_id,canonical_name,parameter_name,ai_value,ai_unit,
          condition,scope,source_page,source_text,confidence,extraction_method) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
          ('c','d','tbw','tbw','600','TB','','product family',1,'TBW 600 TB',.95,'agent_single_pass'))
        for eid, sid in (('e1','s1'),('e2','official_web')):
            con.execute("INSERT INTO candidate_evidence VALUES (?,?,?,?,?,?,?,?)",
                        (eid,'c',1,'Endurance','TBW 600 TB',.95,'agent_single_pass','product family'))
            con.execute("INSERT INTO candidate_evidence_provenance VALUES (?,?)", (eid,sid))
    core.rebuild_reviewed_specifications('d')
    spec = core.list_reviewed_specifications('d')[0]
    assert {e['source_id'] for e in spec['evidence']} == {'s1','official_web'}
    conclusion = core.get_device_conclusion('d')
    tbw = next(x for x in conclusion['key_specs'] if x['canonical_name'] == 'tbw')
    assert {e['source_id'] for e in tbw['evidence']} == {'s1','official_web'}


def test_d2_specification_status_api_and_ui_expose_confirmation_state(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, 'eMMC', [('life_time_a','01h','',''),('life_time_b','01h','',''),('pre_eol','01h','','')])
    client=TestClient(app)
    status=client.get('/api/devices/d/specification-status')
    assert status.status_code == 200
    assert status.json()['status'] == 'pending_confirmation'
    assert status.json()['formal_ready'] is False
    html=client.get('/').text
    assert '草稿，待人工确认关键规格' in html
    assert '已人工确认，可作为正式结论' in html
