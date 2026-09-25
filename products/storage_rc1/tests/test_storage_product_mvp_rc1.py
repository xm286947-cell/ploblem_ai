from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from storage_life import core
from storage_life.app import app


def _seed(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(core, "DATA", tmp_path)
    monkeypatch.setattr(core, "DB", tmp_path / "storage_life.sqlite3")
    with core.connect() as con:
        con.execute("INSERT INTO sources VALUES (?,?,?,?,?,?,?,?)", ("s1","emmc.pdf","x",str(tmp_path/'emmc.pdf'),"https://example.invalid/emmc","Vendor",2,core.now()))
        con.execute("INSERT INTO sources VALUES (?,?,?,?,?,?,?,?)", ("s2","ssd.pdf","y",str(tmp_path/'ssd.pdf'),"https://example.invalid/ssd","Vendor",2,core.now()))
        con.execute("INSERT INTO devices VALUES (?,?,?,?,?)", ("d1","VendorA","EMMC-A","eMMC","s1"))
        con.execute("INSERT INTO devices VALUES (?,?,?,?,?)", ("d2","VendorB","SSD-B","SSD","s2"))
        con.execute("""INSERT INTO candidates(id,device_id,canonical_name,parameter_name,ai_value,ai_unit,final_value,final_unit,condition,scope,source_page,source_section,source_text,confidence,extraction_method,verify_status,verified_by,verified_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", ("c1","d1","life_time_a","Life Time A","0x03","","0x03","","EXT_CSD","user area",1,"Health","DEVICE_LIFE_TIME_EST_TYP_A = 0x03",0.99,"agent_text","confirmed","tester",core.now()))
        con.execute("""INSERT INTO candidates(id,device_id,canonical_name,parameter_name,ai_value,ai_unit,final_value,final_unit,condition,scope,source_page,source_section,source_text,confidence,extraction_method,verify_status,verified_by,verified_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", ("c2","d2","tbw","TBW","600","TB","600","TB","rated","device",1,"Endurance","TBW 600 TB",0.99,"agent_text","confirmed","tester",core.now()))
        coverage1={"states":[{"field_key":"life_time_a","state":"FOUND","reason":"value_and_evidence_valid"},{"field_key":"life_time_b","state":"NOT_SPECIFIED","reason":"relevant_section_searched_without_supported_value"},{"field_key":"pre_eol","state":"UNRESOLVED","reason":"relevant_section_not_searched_or_evidence_invalid"}],"layers":{},"critical_unresolved":[],"identity_unresolved":[],"diagnostic_unresolved":[]}
        coverage2={"states":[{"field_key":"tbw","state":"FOUND","reason":"value_and_evidence_valid"},{"field_key":"smart_health","state":"UNRESOLVED","reason":"ambiguous_or_conflict"}],"layers":{},"critical_unresolved":[],"identity_unresolved":[],"diagnostic_unresolved":[]}
        for rid,did,cov in (("r1","d1",coverage1),("r2","d2",coverage2)):
            con.execute("""INSERT INTO extraction_runs(id,device_id,source_id,extraction_mode,schema_valid,model_calls,unresolved_evidence_json,review_required,review_queue_json,facts_json,expected_fields_json,searched_pages_json,searched_sections_json,searched_fields_json,coverage_json,coverage_layers_json,document_analysis_json,created_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (rid,did,"s1" if did=="d1" else "s2","test",1,1,"[]",0,"[]","[]","[]","[1]","[]","{}",json.dumps(cov),"{}","{}",core.now()))
    (tmp_path/'emmc.pdf').write_bytes(b'%PDF sample')
    (tmp_path/'ssd.pdf').write_bytes(b'%PDF sample')


def test_p01_p08_product_api_and_coverage_semantics(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    client=TestClient(app)
    dash=client.get('/api/product/dashboard').json()
    assert dash['device_count']==2
    detail=client.get('/api/product/devices/d1').json()
    by={x['canonical_name']:x for x in detail['slots']}
    assert by['life_time_a']['status']=='CONFIRMED'
    assert by['life_time_b']['status']=='NOT_FOUND'
    assert by['pre_eol']['status']=='NOT_CHECKED'
    cmp=client.post('/api/product/compare',json={'device_ids':['d1','d2']}).json()
    assert cmp['rows'] and any(r['has_missing'] for r in cmp['rows'])
    diag=client.get('/api/product/diagnostics?device_id=d1').json()
    assert diag['layers']==['DATASHEET_FACT','RUNTIME_OBSERVATION','KNOWLEDGE']
    impact=client.get('/api/product/change-impact?old_id=d1&new_id=d2').json()
    assert impact['final_replacement_decision'] is None
    maintenance=client.get('/api/product/maintenance').json()
    assert maintenance['publish_gate']['storage_can_publish'] is False


def test_formal_release_consumer_uses_release_contract_not_internal_db(monkeypatch):
    seed=Path(__file__).resolve().parents[1]/'knowledge_release'/'seed'
    monkeypatch.setenv('STORAGE_KNOWLEDGE_RELEASE_DIR',str(seed))
    client=TestClient(app)
    status=client.get('/api/product/knowledge/status').json()
    assert status['available'] is True
    assert status['knowledge_release_version']=='SAMPLE-CONTRACT-SMOKE-V1'
    result=client.get('/api/product/knowledge/query?q=Percentage%20Used&device_type=SSD').json()
    assert result['contract_version']=='knowledge-query/v1'
    assert result['results'][0]['object_id']=='KO-SAMPLE-NVME-001'


def test_default_release_is_formal_and_not_sample(monkeypatch):
    monkeypatch.delenv('STORAGE_KNOWLEDGE_RELEASE_DIR',raising=False)
    client=TestClient(app)
    status=client.get('/api/product/knowledge/status').json()
    assert status['available'] is True
    assert status['status']=='READY'
    assert status['knowledge_release_version']=='KP-STORAGE-RC1-VALIDATION-001'
    assert status['knowledge_release_version']!='SAMPLE-CONTRACT-SMOKE-V1'
