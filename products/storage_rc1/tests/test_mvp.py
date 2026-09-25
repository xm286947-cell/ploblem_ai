from pathlib import Path
import shutil

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader, PdfWriter

from storage_life import core, ai
from storage_life.app import app


def test_full_spec_chain(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_LIFE_ENABLE_RULE_FALLBACK", "1")
    monkeypatch.setattr(core, "DATA", tmp_path)
    monkeypatch.setattr(core, "DB", tmp_path / "test.sqlite3")
    client = TestClient(app)
    fixtures = Path(__file__).parents[1] / "examples"
    imported = []
    for kind, file in (("SSD", "synthetic_ssd.pdf"), ("eMMC", "synthetic_emmc.pdf"),
                       ("Raw NAND", "synthetic_raw_nand.pdf")):
        with (fixtures / file).open("rb") as stream:
            response = client.post("/api/documents", data={"vendor": "Synthetic", "model": file,
                "device_type": kind, "original_url": "https://example.invalid/synthetic"},
                files={"file": (file, stream, "application/pdf")})
        assert response.status_code == 201, response.text
        imported.append(response.json()["device_id"])
        assert response.json()["candidate_count"] >= 3
    first = client.get(f"/api/devices/{imported[0]}/candidates").json()
    assert all(c["source_page"] == 1 and c["source_text"] for c in first)
    assert client.get("/api/knowledge/search", params={"q": "TBW"}).json()["status"] == "no_evidence"
    for device in imported:
        for c in client.get(f"/api/devices/{device}/candidates").json():
            response = client.patch(f"/api/candidates/{c['id']}", json={"status": "confirmed",
                "value": c["ai_value"], "unit": c["ai_unit"], "verified_by": "fixture reviewer"})
            assert response.status_code == 200
    result = client.post("/api/compare", json={"device_ids": imported}).json()
    assert len(result["devices"]) == 3 and "capacity" in result["fields"]
    answer = client.get("/api/knowledge/search", params={"q": "TBW"}).json()
    assert answer["status"] == "evidenced" and answer["facts"][0]["source_page"] == 1
    assert client.get("/api/impact-drafts", params={"old_id": imported[0], "new_id": imported[1]}).json()["status"] == "draft_for_review"
    source = client.get("/api/devices").json()[0]["source_id"]
    assert client.get(f"/api/sources/{source}/pdf?page=1").status_code == 200
    assert client.post("/api/links", json={"device_id": imported[0], "target_system": "quality_issue",
        "target_id": "SYN-CASE-001", "relation": "related"}).status_code == 201
    assert client.get("/api/links", params={"target_id": "SYN-CASE-001"}).json()[0]["device_id"] == imported[0]
    cases = client.get("/api/external/cases", params={"keyword": "SSD"}).json()
    assert len(cases) == 1 and cases[0]["case_id"] == "SYN-CASE-001"
    assert client.get("/api/external/cases/SYN-CASE-001").json()["synthetic"] is True


def test_invalid_pdf_has_clear_error():
    client = TestClient(app)
    response = client.post("/api/documents", data={"vendor": "X", "model": "Y", "device_type": "SSD"},
        files={"file": ("broken.pdf", b"not a pdf", "application/pdf")})
    assert response.status_code == 422


def test_pdf_over_50_pages_is_allowed(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DATA", tmp_path)
    monkeypatch.setattr(core, "DB", tmp_path / "test.sqlite3")
    fixture = Path(__file__).parents[1] / "examples" / "synthetic_ssd.pdf"
    source_page = PdfReader(fixture).pages[0]
    writer = PdfWriter()
    for _ in range(51):
        writer.add_page(source_page)
    from io import BytesIO
    stream = BytesIO()
    writer.write(stream)
    client = TestClient(app)
    response = client.post("/api/documents", data={"vendor": "Synthetic", "model": "51-pages",
        "device_type": "SSD"}, files={"file": ("51-pages.pdf", stream.getvalue(), "application/pdf")})
    assert response.status_code == 201, response.text
    source_id = client.get("/api/devices").json()[0]["source_id"]
    assert client.get(f"/api/sources/{source_id}/pdf?page=51").status_code == 200


@pytest.mark.skipif(not (shutil.which("tesseract") and shutil.which("pdftoppm")), reason="OCR tools unavailable")
def test_scanned_and_mixed_pdf_page_provenance(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_LIFE_ENABLE_RULE_FALLBACK", "1")
    monkeypatch.setattr(core, "DATA", tmp_path)
    monkeypatch.setattr(core, "DB", tmp_path / "test.sqlite3")
    fixtures = Path(__file__).parents[1] / "examples"
    writer = PdfWriter()
    writer.add_page(PdfReader(fixtures / "synthetic_ssd.pdf").pages[0])
    writer.add_page(PdfReader(fixtures / "synthetic_scanned_ssd.pdf").pages[0])
    from io import BytesIO
    stream = BytesIO()
    writer.write(stream)
    client = TestClient(app)
    response = client.post("/api/documents", data={"vendor": "Synthetic", "model": "mixed",
        "device_type": "SSD"}, files={"file": ("mixed.pdf", stream.getvalue(), "application/pdf")})
    assert response.status_code == 201, response.text
    assert response.json()["ocr_pages"] == [2]
    candidates = client.get(f"/api/devices/{response.json()['device_id']}/candidates").json()
    assert any(c["source_page"] == 1 and c["extraction_method"] == "text" for c in candidates)
    assert any(c["source_page"] == 2 and c["extraction_method"] == "ocr" for c in candidates)
    assert all(c["verify_status"] == "pending" for c in candidates)
    capacities = [c for c in candidates if c["canonical_name"] == "capacity"]
    assert len(capacities) == 2
    body = {"status": "confirmed", "verified_by": "reviewer"}
    assert client.patch(f"/api/candidates/{capacities[0]['id']}", json=body).status_code == 200
    assert client.patch(f"/api/candidates/{capacities[1]['id']}", json=body).status_code == 409


def test_gigadevice_style_feature_page_has_reviewable_conditions():
    page = """◆ 1Gb SLC NAND Flash
- Internal ECC On (ECC_EN=1, default):
Page Size：2048-Byte+64-Byte
- Internal ECC Off (ECC_EN=0):
Page Size：2048-Byte+128-Byte
- P/E cycles with ECC: 100K
- Data retention: 10 Years
- 4bits /528byte
"""
    found = core.candidates_from_pages([(4, page, 'text')])
    assert {c['canonical_name'] for c in found} >= {'capacity', 'page_size', 'pe_cycles', 'retention', 'ecc_requirement'}
    sizes = [c for c in found if c['canonical_name'] == 'page_size']
    assert {c['condition'] for c in sizes} == {'Internal ECC On', 'Internal ECC Off'}
    assert all(c['source_page'] == 4 and c['source_text'] for c in found)


def test_pdf_identity_preview_and_expected_fields(monkeypatch):
    from storage_life import ai
    monkeypatch.setattr(core, 'extract_pdf', lambda data: [(1, 'GigaDevice GD5F1GQ5UExxG SPI-NAND', 'text')])
    monkeypatch.setattr(ai, 'identify_device', lambda pages: {
        'vendor': {'value': 'GigaDevice', 'page': 1, 'quote': 'GigaDevice', 'confidence': .99},
        'model': {'value': 'GD5F1GQ5UExxG', 'page': 1, 'quote': 'GD5F1GQ5UExxG', 'confidence': .98},
        'device_type': {'value': 'Raw NAND', 'page': 1, 'quote': 'SPI-NAND', 'confidence': .97},
        'analyzed_pages': [1],
    })
    client = TestClient(app)
    r = client.post('/api/documents/identify', files={'file': ('x.pdf', b'%PDF-test', 'application/pdf')})
    assert r.status_code == 200
    assert r.json()['vendor']['value'] == 'GigaDevice'
    fields = client.get('/api/spec-fields', params={'device_type': 'Raw NAND'}).json()
    assert any(x['canonical_name'] == 'pe_cycles' for x in fields)
    assert any(x['canonical_name'] == 'minimum_valid_blocks' for x in fields)


def test_v055_database_migrates_device_vocabulary(tmp_path, monkeypatch):
    import sqlite3
    db = tmp_path / 'legacy.sqlite3'
    con = sqlite3.connect(db)
    con.executescript("""
    CREATE TABLE sources(id TEXT PRIMARY KEY, filename TEXT, sha256 TEXT, local_path TEXT, original_url TEXT, publisher TEXT, page_count INTEGER, created_at TEXT);
    CREATE TABLE devices(id TEXT PRIMARY KEY, vendor TEXT, model TEXT, device_type TEXT CHECK(device_type IN ('SSD','eMMC','Raw NAND')), source_id TEXT REFERENCES sources(id));
    CREATE TABLE candidates(id TEXT PRIMARY KEY, device_id TEXT REFERENCES devices(id), canonical_name TEXT, parameter_name TEXT, ai_value TEXT, ai_unit TEXT, final_value TEXT, final_unit TEXT, condition TEXT DEFAULT '', source_page INTEGER, source_section TEXT DEFAULT '', source_text TEXT, confidence REAL, extraction_method TEXT DEFAULT 'text', verify_status TEXT DEFAULT 'pending', verified_by TEXT, verified_at TEXT);
    CREATE TABLE links(id TEXT PRIMARY KEY, device_id TEXT REFERENCES devices(id), target_system TEXT, target_id TEXT, relation TEXT, created_at TEXT, UNIQUE(device_id,target_system,target_id,relation));
    INSERT INTO sources VALUES ('s','x.pdf','h','/tmp/x.pdf','','',1,'now');
    INSERT INTO devices VALUES ('d','GigaDevice','GD5F','Raw NAND','s');
    INSERT INTO candidates(id,device_id,canonical_name,parameter_name,ai_value,ai_unit,source_page,source_text,confidence) VALUES ('c','d','capacity','Capacity','1Gb','',1,'1Gb',0.9);
    """)
    con.commit(); con.close()
    monkeypatch.setattr(core, 'DATA', tmp_path)
    monkeypatch.setattr(core, 'DB', db)
    with core.connect() as upgraded:
        assert upgraded.execute("SELECT device_type FROM devices WHERE id='d'").fetchone()[0] == 'NAND Flash'
        upgraded.execute("INSERT INTO devices VALUES ('n','ISSI','N1','NOR Flash','s')")
        assert upgraded.execute("SELECT COUNT(*) FROM candidates WHERE id='c'").fetchone()[0] == 1


def test_document_can_persist_multiple_model_candidates(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'DATA', tmp_path)
    monkeypatch.setattr(core, 'DB', tmp_path / 'test.sqlite3')
    monkeypatch.setattr(core, 'extract_pdf', lambda data: [(1, 'family datasheet', 'text')])
    imported = core.import_document('family.pdf', b'%PDF-test', 'GigaDevice', 'GD5F-family', 'NAND Flash',
        model_candidates=[
            {'value':'GD5F1GQ5UExxG','scope':'3.3V','page':1,'quote':'GD5F1GQ5UExxG','confidence':.9},
            {'value':'GD5F1GQ5RExxG','scope':'1.8V','page':1,'quote':'GD5F1GQ5RExxG','confidence':.9},
        ])
    models = core.list_models(imported['device_id'])
    assert imported['model_candidate_count'] == 2
    assert {m['ai_model'] for m in models} == {'GD5F1GQ5UExxG','GD5F1GQ5RExxG'}
    assert all(m['verify_status'] == 'pending' for m in models)
    core.verify_model(models[0]['id'], 'confirmed', models[0]['ai_model'], 'reviewer', models[0]['scope'])
    assert core.list_models(imported['device_id'])[0]['verify_status'] == 'confirmed'


def _seed_cleanup_case(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'DATA', tmp_path)
    monkeypatch.setattr(core, 'DB', tmp_path / 'cleanup.sqlite3')
    with core.connect() as con:
        con.execute("INSERT INTO sources VALUES (?,?,?,?,?,?,?,?)",
                    ('s1','test.pdf','hash',str(tmp_path/'test.pdf'),'','','1','now'))
        con.execute("INSERT INTO devices VALUES (?,?,?,?,?)", ('d1','GigaDevice','GD5F-family','NAND Flash','s1'))
        con.execute("""INSERT INTO document_models
          (id,source_id,device_id,ai_model,scope,source_page,source_text,confidence)
          VALUES (?,?,?,?,?,?,?,?)""", ('m1','s1','d1','GD5F1GQ5U','3.3V',1,'GD5F1GQ5U',.9))
        for cid, field, value in [('c1','capacity','1 Gb'),('c2','pe_cycles','100K')]:
            con.execute("""INSERT INTO candidates(id,device_id,canonical_name,parameter_name,ai_value,ai_unit,
              source_page,source_text,confidence,extraction_method) VALUES (?,?,?,?,?,?,?,?,?,?)""",
              (cid,'d1',field,field,value,'',1,value,.9,'agent_text'))
            con.execute("INSERT INTO candidate_evidence VALUES (?,?,?,?,?,?,?,?)",
                        ('e'+cid,cid,1,'','evidence '+value,.9,'agent_text',''))
        con.execute("INSERT INTO final_reviews VALUES (?,?,?,?,?,?,?)",
                    ('r1','d1','attention_required','review','[]','[]','now'))
    return TestClient(app)


def test_delete_single_candidate_cleans_evidence_and_invalidates_review(tmp_path, monkeypatch):
    client = _seed_cleanup_case(tmp_path, monkeypatch)
    response = client.delete('/api/candidates/c1')
    assert response.status_code == 200
    with core.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM candidates WHERE id='c1'").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM candidate_evidence WHERE candidate_id='c1'").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM candidates WHERE id='c2'").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM final_reviews WHERE device_id='d1'").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM document_models WHERE id='m1'").fetchone()[0] == 1


def test_clear_candidates_keeps_pdf_device_and_models(tmp_path, monkeypatch):
    client = _seed_cleanup_case(tmp_path, monkeypatch)
    response = client.delete('/api/devices/d1/candidates')
    assert response.status_code == 200
    assert response.json()['deleted_count'] == 2
    with core.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM candidates WHERE device_id='d1'").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM candidate_evidence").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM final_reviews WHERE device_id='d1'").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM devices WHERE id='d1'").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM sources WHERE id='s1'").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM document_models WHERE id='m1'").fetchone()[0] == 1


def test_import_document_reports_real_progress(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'DATA', tmp_path)
    monkeypatch.setattr(core, 'DB', tmp_path / 'progress.sqlite3')
    monkeypatch.setattr(core, 'extract_pdf', lambda data: [(1, 'Capacity 1 GB', 'text')])
    stages = []
    core.import_document('progress.pdf', b'%PDF-test', 'Synthetic', 'P1', 'SSD', progress=lambda stage, pct, msg: stages.append((stage, pct, msg)))
    names = [x[0] for x in stages]
    assert names[0] == 'validate'
    assert 'parse_pdf' in names and 'read_plan' in names and 'persist' in names
    assert names[-1] == 'completed' and stages[-1][1] == 100


def test_async_import_job_exposes_status(monkeypatch):
    import time as _time
    from storage_life import app as app_module

    def fake_import(filename, data, vendor, model, device_type, original_url='', publisher='', model_candidates=None, progress=None):
        if progress:
            progress('parse_pdf', 18, '正在解析 PDF 文本和页码…')
        _time.sleep(0.05)
        if progress:
            progress('agent_extract', 45, 'Agent 正在抽取参数…')
        _time.sleep(0.05)
        return {'device_id':'d-job','source_id':'s-job','candidate_count':2,'model_candidate_count':1,
                'extraction_mode':'agent_template','analyzed_pages':[1],'planned_pages':[1],
                'template':{},'ocr_pages':[]}

    monkeypatch.setattr(core, 'import_document', fake_import)
    client = TestClient(app)
    response = client.post('/api/documents/jobs', data={'vendor':'GigaDevice','model':'GD5F','device_type':'NAND Flash'},
                           files={'file':('sample.pdf', b'%PDF-test', 'application/pdf')})
    assert response.status_code == 202
    job_id = response.json()['job_id']
    seen_running = False
    final = None
    for _ in range(30):
        state = client.get(f'/api/documents/jobs/{job_id}')
        assert state.status_code == 200
        payload = state.json()
        seen_running = seen_running or payload['status'] == 'running'
        if payload['status'] in {'completed','failed'}:
            final = payload
            break
        _time.sleep(0.02)
    assert final is not None and final['status'] == 'completed'
    assert final['progress'] == 100 and final['result']['candidate_count'] == 2
    assert seen_running


def test_delete_whole_datasheet_record_cleans_graph_and_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'DATA', tmp_path)
    monkeypatch.setattr(core, 'DB', tmp_path / 'delete-device.sqlite3')
    pdf = tmp_path / 'documents' / 'owned.pdf'
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(b'%PDF-owned')
    with core.connect() as con:
        con.execute("INSERT INTO sources VALUES (?,?,?,?,?,?,?,?)",
                    ('s-del','owned.pdf','hash',str(pdf),'https://example.com/owned.pdf','Vendor',3,'now'))
        con.execute("INSERT INTO devices VALUES (?,?,?,?,?)", ('d-del','ISSI','IS37-family','NAND Flash','s-del'))
        con.execute("""INSERT INTO document_models
          (id,source_id,device_id,ai_model,scope,source_page,source_text,confidence)
          VALUES (?,?,?,?,?,?,?,?)""", ('m-del','s-del','d-del','IS37SML01G1','',1,'IS37SML01G1',.9))
        con.execute("""INSERT INTO candidates(id,device_id,canonical_name,parameter_name,ai_value,ai_unit,
          source_page,source_text,confidence,extraction_method) VALUES (?,?,?,?,?,?,?,?,?,?)""",
          ('c-del','d-del','capacity','Capacity','1 Gb','',1,'1Gb',.9,'agent_text'))
        con.execute("INSERT INTO candidate_evidence VALUES (?,?,?,?,?,?,?,?)",
                    ('e-del','c-del',1,'FEATURES','1Gb SLC',.9,'agent_text',''))
        con.execute("INSERT INTO final_reviews VALUES (?,?,?,?,?,?,?)",
                    ('r-del','d-del','ready_for_human_review','ok','[]','[]','now'))
        con.execute("INSERT INTO links VALUES (?,?,?,?,?,?)",
                    ('l-del','d-del','quality_issue','CASE-1','related','now'))
    client = TestClient(app)
    response = client.delete('/api/devices/d-del')
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['candidate_count'] == 1 and payload['model_candidate_count'] == 1
    assert payload['link_count'] == 1 and payload['review_count'] == 1
    assert payload['source_deleted'] is True and payload['file_deleted'] is True
    assert not pdf.exists()
    with core.connect() as con:
        for table in ('devices','sources','candidates','candidate_evidence','document_models','final_reviews','links'):
            assert con.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0


def test_multiple_imported_specs_can_delete_one_without_touching_others(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'DATA', tmp_path)
    monkeypatch.setattr(core, 'DB', tmp_path / 'multi-delete.sqlite3')
    monkeypatch.setattr(core, 'extract_pdf', lambda data: [(1, 'FEATURES Capacity 1 GB', 'text')])
    first = core.import_document('a.pdf', b'a', 'GigaDevice', 'A', 'NAND Flash')
    second = core.import_document('b.pdf', b'b', 'ISSI', 'B', 'NOR Flash')
    client = TestClient(app)
    rows = client.get('/api/devices').json()
    assert {x['id'] for x in rows} == {first['device_id'], second['device_id']}
    assert all('candidate_count' in x and 'model_candidate_count' in x for x in rows)
    assert client.delete(f"/api/devices/{first['device_id']}").status_code == 200
    remaining = client.get('/api/devices').json()
    assert len(remaining) == 1 and remaining[0]['id'] == second['device_id']
    assert client.get(f"/api/sources/{second['source_id']}/pdf?page=1").status_code == 200


def test_real_vendor_template_scenarios_have_expected_read_targets():
    from storage_life import templates
    scenarios = [
        ('GigaDevice', 'NOR Flash', [
            (1, 'FEATURES Serial NOR Flash Capacity SPI Quad Operating Voltage Clock Frequency Endurance Data Retention', 'text'),
            (12, 'MEMORY ORGANIZATION MEMORY ARRAY', 'text'),
            (30, 'AC CHARACTERISTICS PROGRAM ERASE ORDERING INFORMATION', 'text'),
        ], {'capacity','interface','voltage','clock_frequency','pe_cycles','retention','program_time','erase_time'}),
        ('GigaDevice', 'NAND Flash', [
            (1, 'CONTENTS FEATURE GENERAL DESCRIPTION VALID PART NUMBERS ARRAY ORGANIZATION READ PARAMETER PAGE PERFORMANCE AND TIMING ORDERING INFORMATION', 'text'),
            (4, '1 FEATURE 1Gb SLC NAND Flash Internal ECC P/E cycles Data retention', 'text'),
            (48, 'ASSISTANT BAD BLOCK MANAGEMENT', 'text'),
            (51, 'INTERNAL ECC', 'text'),
            (58, '18 PERFORMANCE AND TIMING', 'text'),
        ], {'capacity','page_size','bad_block_mark','ecc_capability','program_time','erase_time'}),
        ('Macronix', 'NOR Flash', [
            (1, 'FEATURES Serial NOR Flash 128Mb 2.7V-3.6V Quad I/O 133MHz', 'text'),
            (8, 'MEMORY ORGANIZATION ERASE ARCHITECTURE', 'text'),
            (25, 'AC CHARACTERISTICS PROGRAM ERASE ORDERING INFORMATION', 'text'),
        ], {'capacity','interface','voltage','clock_frequency','erase_granularity','program_time','erase_time'}),
        ('Macronix', 'NAND Flash', [
            (1, 'FEATURES SLC NAND Flash 1Gb 2.7V-3.6V Page Size ECC Requirement', 'text'),
            (10, 'MEMORY ORGANIZATION ARRAY ORGANIZATION', 'text'),
            (30, 'BAD BLOCK MANAGEMENT ECC REQUIREMENT RELIABILITY', 'text'),
        ], {'capacity','cell_type','page_size','ecc_capability','minimum_valid_blocks','pe_cycles','retention'}),
        ('ATMEL', 'NOR Flash', [
            (1, 'FEATURES Endurance Data retention', 'text'),
            (6, '4. Memory Array', 'text'),
            (20, 'PROGRAM AND ERASE COMMANDS BLOCK ERASE CHIP ERASE', 'text'),
        ], {'capacity','erase_granularity','program_time','erase_time','pe_cycles'}),
        ('ISSI', 'NOR Flash', [
            (1, 'FEATURES Flexible & Efficient Memory Architecture 100,000 Erase/Program Cycles Data Retention', 'text'),
            (20, 'AC CHARACTERISTICS PROGRAM ERASE', 'text'),
        ], {'capacity','pe_cycles','retention','program_time','erase_time'}),
        ('ISSI', 'NAND Flash', [
            (1, 'FEATURES 1Gb SLC SERIAL NAND FLASH', 'text'),
            (10, 'MEMORY ORGANIZATION ARRAY ORGANIZATION', 'text'),
            (30, 'BAD BLOCK MANAGEMENT INTERNAL ECC RELIABILITY', 'text'),
        ], {'capacity','page_size','bad_block_mark','ecc_capability'}),
        ('SkyHigh Memory', 'eMMC', [
            (1, 'Features e.MMC 5.1 Density Operating Voltage Operating Temperature Health Monitoring', 'text'),
            (13, '6.5 Extended CSD Register EXT_CSD DEVICE_LIFE_TIME_EST_TYP_A PRE_EOL_INFO', 'text'),
            (31, '9. Ordering Information', 'text'),
        ], {'capacity','life_time_a','life_time_b','pre_eol','ext_csd_health_report'}),
        ('TIMAR', 'eMMC', [
            (1, 'KEY FEATURES PRODUCT SPECIFICATION eMMC5.1 TLC 64GB HS400 Operation Temperature P/E CYCLES Power Loss Protection', 'text'),
            (4, 'TECHNOLOGY APPLICATION HEALTH', 'text'),
        ], {'capacity','cell_type','pe_cycles','plp'}),
        ('TIMAR', 'SSD', [
            (2, 'Key Features Capacity PCIe NVMe TBW Temperatures MTBF UBER PLP', 'text'),
            (5, '1.2 Product Line-up Performance', 'text'),
        ], {'capacity','tbw','plp'}),
    ]
    for vendor, dtype, pages, expected in scenarios:
        summary = templates.template_summary(dtype, vendor)
        assert summary['vendor_template_supported'] is True, (vendor, dtype)
        plan = templates.build_read_plan(pages, dtype, vendor)
        targets = {f for item in plan for f in item['target_fields']}
        assert expected <= targets, (vendor, dtype, expected - targets)


def test_home_exposes_imported_datasheet_record_management():
    client = TestClient(app)
    html = client.get('/').text
    assert '已导入规格书' in html
    assert 'deleteDevice(' in html
    assert '删除当前规格书' in html


def test_family_view_separates_common_specs_and_part_number_differences(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'DATA', tmp_path)
    monkeypatch.setattr(core, 'DB', tmp_path / 'family-view.sqlite3')
    with core.connect() as con:
        con.execute("INSERT INTO sources VALUES (?,?,?,?,?,?,?,?)",
                    ('s-f','family.pdf','hash',str(tmp_path/'family.pdf'),'','','10','now'))
        con.execute("INSERT INTO devices VALUES (?,?,?,?,?)", ('d-f','GigaDevice','GD5F1GQ5xExxG','NAND Flash','s-f'))
        con.execute("""INSERT INTO document_models
          (id,source_id,device_id,ai_model,scope,source_page,source_text,confidence)
          VALUES (?,?,?,?,?,?,?,?)""", ('m-u','s-f','d-f','GD5F1GQ5UExxG','3.3V family',1,'U family',.9))
        con.execute("""INSERT INTO document_models
          (id,source_id,device_id,ai_model,scope,source_page,source_text,confidence)
          VALUES (?,?,?,?,?,?,?,?)""", ('m-r','s-f','d-f','GD5F1GQ5RExxG','1.8V family',1,'R family',.9))
        specs = [
            ('c-cap','capacity','Capacity','1Gb','','product family','Density is 1Gb'),
            ('c-u','voltage','Operating Voltage','2.7-3.6V','','3.3V family','2.7-3.6V'),
            ('c-r','voltage','Operating Voltage','1.7-2.0V','','1.8V family','1.7-2.0V'),
        ]
        for cid, canonical, label, value, condition, scope, quote in specs:
            con.execute("""INSERT INTO candidates(id,device_id,canonical_name,parameter_name,ai_value,ai_unit,
              condition,scope,source_page,source_text,confidence,extraction_method) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
              (cid,'d-f',canonical,label,value,'',condition,scope,1,quote,.9,'agent_text'))
            con.execute("INSERT INTO candidate_evidence VALUES (?,?,?,?,?,?,?,?)",
                        ('e-'+cid,cid,1,'',quote,.9,'agent_text',scope))
    view = core.family_view('d-f')
    assert view['counts']['models'] == 2
    assert any(x['canonical_name'] == 'capacity' for x in view['common_specs'])
    voltage = next(x for x in view['difference_rows'] if x['canonical_name'] == 'voltage')
    assert voltage['cells']['m-u'][0]['value'] == '2.7-3.6V'
    assert voltage['cells']['m-r'][0]['value'] == '1.7-2.0V'
    assert not any(x['canonical_name'] == 'capacity' for x in view['matrix_rows'])
    common_capacity = next(x for x in view['common_specs'] if x['canonical_name'] == 'capacity')
    assert common_capacity['display_value'] == '1 Gbit'


def test_family_view_collapses_equivalent_common_candidates_and_keeps_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'DATA', tmp_path)
    monkeypatch.setattr(core, 'DB', tmp_path / 'family-dedupe.sqlite3')
    with core.connect() as con:
        con.execute("INSERT INTO sources VALUES (?,?,?,?,?,?,?,?)", ('s','x.pdf','h',str(tmp_path/'x.pdf'),'','','2','now'))
        con.execute("INSERT INTO devices VALUES (?,?,?,?,?)", ('d','GigaDevice','GD5F1GQ5xExxG','NAND Flash','s'))
        con.execute("""INSERT INTO document_models(id,source_id,device_id,ai_model,scope,source_page,source_text,confidence)
          VALUES (?,?,?,?,?,?,?,?)""", ('m','s','d','GD5F1GQ5UEYIG','3.3V family',1,'pn',.9))
        for i,(value,unit,page) in enumerate([('1Gb','bit',1),('1Gbit','',2),('1G','bit',2)]):
            cid=f'c{i}'
            con.execute("""INSERT INTO candidates(id,device_id,canonical_name,parameter_name,ai_value,ai_unit,condition,scope,source_page,source_text,confidence,extraction_method)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (cid,'d','capacity','Capacity',value,unit,'','product family',page,value,.9,'agent_text'))
            con.execute("INSERT INTO candidate_evidence VALUES (?,?,?,?,?,?,?,?)", (f'e{i}',cid,page,'',value,.9,'agent_text','product family'))
    view=core.family_view('d')
    caps=[x for x in view['common_specs'] if x['canonical_name']=='capacity']
    assert len(caps)==1
    assert caps[0]['display_value']=='1 Gbit'
    assert caps[0]['evidence_count']==3
    assert not any(x['canonical_name']=='capacity' for x in view['matrix_rows'])


def test_final_review_applies_evidence_grounded_correction_but_keeps_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'DATA', tmp_path)
    monkeypatch.setattr(core, 'DB', tmp_path / 'review-correction.sqlite3')
    with core.connect() as con:
        con.execute("INSERT INTO sources VALUES (?,?,?,?,?,?,?,?)", ('s','x.pdf','h',str(tmp_path/'x.pdf'),'','','1','now'))
        con.execute("INSERT INTO devices VALUES (?,?,?,?,?)", ('d','GigaDevice','GD5F','NAND Flash','s'))
        con.execute("""INSERT INTO candidates(id,device_id,canonical_name,parameter_name,ai_value,ai_unit,
          condition,scope,source_page,source_text,confidence,extraction_method) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
          ('c','d','voltage','Operating Voltage','2.7','','','product family',1,'Operating Voltage: 2.7-3.6V',.9,'agent_text'))
        con.execute("INSERT INTO candidate_evidence VALUES (?,?,?,?,?,?,?,?)",
                    ('e','c',1,'Electrical','Operating Voltage: 2.7-3.6V',.9,'agent_text','3.3V family'))
    saved = core.save_final_review('d', {
        'overall_status':'attention_required','summary':'Correct variant voltage.','missing_fields':[],'findings':[],
        'corrections':[{'candidate_id':'c','action':'update','proposed_value':'2.7-3.6V','proposed_unit':'V',
                        'proposed_condition':'','proposed_scope':'3.3V family','reason':'Evidence gives the full range.'}]
    })
    candidate = core.list_candidates('d')[0]
    assert candidate['ai_value'] == '2.7'
    assert candidate['final_value'] == '2.7-3.6V'
    assert candidate['final_unit'] == 'V'
    assert candidate['scope'] == '3.3V family'
    assert candidate['verify_status'] == 'pending'
    assert saved['applied_correction_count'] == 1
    assert saved['corrections'][0]['applied'] is True


def test_final_review_never_overwrites_human_confirmed_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'DATA', tmp_path)
    monkeypatch.setattr(core, 'DB', tmp_path / 'review-human.sqlite3')
    with core.connect() as con:
        con.execute("INSERT INTO sources VALUES (?,?,?,?,?,?,?,?)", ('s','x.pdf','h',str(tmp_path/'x.pdf'),'','','1','now'))
        con.execute("INSERT INTO devices VALUES (?,?,?,?,?)", ('d','GigaDevice','GD5F','NAND Flash','s'))
        con.execute("""INSERT INTO candidates(id,device_id,canonical_name,parameter_name,ai_value,ai_unit,
          final_value,final_unit,condition,scope,source_page,source_text,confidence,extraction_method,verify_status,verified_by,verified_at)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          ('c','d','capacity','Capacity','1Gb','','1Gb','','','product family',1,'Density is 1Gb',.9,'agent_text','confirmed','human','now'))
        con.execute("INSERT INTO candidate_evidence VALUES (?,?,?,?,?,?,?,?)",
                    ('e','c',1,'Overview','Density is 1Gb',.9,'agent_text','product family'))
    saved = core.save_final_review('d', {
        'overall_status':'attention_required','summary':'Review.','missing_fields':[],'findings':[],
        'corrections':[{'candidate_id':'c','action':'update','proposed_value':'1Gb','proposed_unit':'bit',
                        'proposed_condition':'','proposed_scope':'variant','reason':'Suggested re-scope.'}]
    })
    candidate = core.list_candidates('d')[0]
    assert candidate['scope'] == 'product family'
    assert candidate['verify_status'] == 'confirmed'
    assert saved['applied_correction_count'] == 0
    assert '不覆盖' in saved['corrections'][0]['apply_note']


def test_home_exposes_product_family_views_and_review_corrections():
    client = TestClient(app)
    html = client.get('/').text
    assert '寿命与诊断' in html and '料号差异' in html and '识别明细' in html
    assert 'Final Review 可以修正待确认候选' in html


def test_final_review_endpoint_updates_family_view_without_confirming(tmp_path, monkeypatch):
    from storage_life import ai
    monkeypatch.setattr(core, 'DATA', tmp_path)
    monkeypatch.setattr(core, 'DB', tmp_path / 'review-route.sqlite3')
    with core.connect() as con:
        con.execute("INSERT INTO sources VALUES (?,?,?,?,?,?,?,?)", ('s','x.pdf','h',str(tmp_path/'x.pdf'),'','','1','now'))
        con.execute("INSERT INTO devices VALUES (?,?,?,?,?)", ('d','GigaDevice','GD5F','NAND Flash','s'))
        con.execute("""INSERT INTO document_models
          (id,source_id,device_id,ai_model,scope,source_page,source_text,confidence)
          VALUES (?,?,?,?,?,?,?,?)""", ('m','s','d','GD5F1GQ5U','3.3V family',1,'GD5F1GQ5U',.9))
        con.execute("""INSERT INTO candidates(id,device_id,canonical_name,parameter_name,ai_value,ai_unit,
          condition,scope,source_page,source_text,confidence,extraction_method) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
          ('c','d','voltage','Operating Voltage','2.7','','','product family',1,'Operating Voltage: 2.7-3.6V',.9,'agent_text'))
        con.execute("INSERT INTO candidate_evidence VALUES (?,?,?,?,?,?,?,?)",
                    ('e','c',1,'Electrical','Operating Voltage: 2.7-3.6V',.9,'agent_text','3.3V family'))
    monkeypatch.setattr(ai, 'final_review', lambda *args, **kwargs: {
        'overall_status':'attention_required','summary':'Corrected candidate.','missing_fields':[],'findings':[],
        'corrections':[{'candidate_id':'c','action':'update','proposed_value':'2.7-3.6V','proposed_unit':'V',
                        'proposed_condition':'','proposed_scope':'3.3V family','reason':'Direct evidence.'}]
    })
    client = TestClient(app)
    response = client.post('/api/devices/d/final-review')
    assert response.status_code == 200
    assert response.json()['applied_correction_count'] == 1
    candidate = client.get('/api/devices/d/candidates').json()[0]
    assert candidate['final_value'] == '2.7-3.6V' and candidate['verify_status'] == 'pending'
    family = client.get('/api/devices/d/family-view').json()
    voltage = next(x for x in family['difference_rows'] if x['canonical_name'] == 'voltage')
    assert voltage['cells']['m'][0]['value'] == '2.7-3.6V'


def test_bilingual_field_labels_and_cell_type_display(tmp_path, monkeypatch):
    from storage_life import templates
    assert templates.fields_for('NAND Flash')['nand_type'] == '单元类型（Cell Type）'
    assert templates.fields_for('NAND Flash')['pe_cycles'] == '擦写次数（P/E Cycle）'
    assert templates.format_spec_value('nand_type', 'SLC NAND Flash', '') == '单层单元（SLC）'
    assert templates.format_spec_value('capacity', '1G-bit', 'bit') == '1 Gbit'
    assert templates.format_spec_value('program_time', '400us', 'us') == '400 μs'


def test_existing_english_candidate_is_presented_with_bilingual_label(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'DATA', tmp_path)
    monkeypatch.setattr(core, 'DB', tmp_path / 'display-label.sqlite3')
    with core.connect() as con:
        con.execute("INSERT INTO sources VALUES (?,?,?,?,?,?,?,?)", ('s','x.pdf','h',str(tmp_path/'x.pdf'),'','','1','now'))
        con.execute("INSERT INTO devices VALUES (?,?,?,?,?)", ('d','GigaDevice','GD5F','NAND Flash','s'))
        con.execute("""INSERT INTO candidates(id,device_id,canonical_name,parameter_name,ai_value,ai_unit,
          condition,scope,source_page,source_text,confidence,extraction_method) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
          ('c','d','nand_type','NAND Type','SLC NAND Flash','','','product family',1,'1Gb SLC NAND Flash',.9,'agent_text'))
    candidate = core.list_candidates('d')[0]
    assert candidate['parameter_name'] == '单元类型（Cell Type）'
    assert candidate['display_ai_value'] == '单层单元（SLC）'
    assert candidate['display_scope'] == '产品族（Product Family）'



def test_reviewed_specs_compacts_timing_and_hides_mechanism_prose(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'DATA', tmp_path)
    monkeypatch.setattr(core, 'DB', tmp_path / 'reviewed-specs.sqlite3')
    with core.connect() as con:
        con.execute("INSERT INTO sources VALUES (?,?,?,?,?,?,?,?)", ('s','x.pdf','h',str(tmp_path/'x.pdf'),'','','2','now'))
        con.execute("INSERT INTO devices VALUES (?,?,?,?,?)", ('d','GigaDevice','GD5F','NAND Flash','s'))
        data = [
            ('c1','program_time','Program Time','300','us','typical','product family','Page Program 300us typical'),
            ('c2','program_time','Program Time','600','us','max','product family','Page Program 600us max'),
            ('c3','ecc_requirement','ECC Requirement','4 bits/528 bytes','','','product family','ECC capability 4 bits/528 bytes'),
            ('c4','ecc_requirement','ECC Requirement','device calculates an ECC code on the 2k page','','Internal ECC enabled','product family','device calculates an ECC code on the 2k page'),
            ('c5','bad_block_requirement','Bad Block Requirement','1004 blocks','','minimum number of valid blocks','product family','1004 blocks minimum number of valid blocks'),
        ]
        for cid,canon,name,val,unit,cond,scope,quote in data:
            con.execute("""INSERT INTO candidates(id,device_id,canonical_name,parameter_name,ai_value,ai_unit,condition,scope,source_page,source_text,confidence,extraction_method)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (cid,'d',canon,name,val,unit,cond,scope,1,quote,.9,'agent_text'))
            con.execute("INSERT INTO candidate_evidence VALUES (?,?,?,?,?,?,?,?)", ('e'+cid,cid,1,'',quote,.9,'agent_text',scope))
    view = core.reviewed_specs('d')
    specs = {x['canonical_name']:x for g in view['groups'] for x in g['specs']}
    assert len(specs['program_time']['items']) == 2
    assert len(specs['ecc_capability']['items']) == 1
    assert specs['ecc_capability']['items'][0]['display'] == '4 bits/528 bytes'
    assert view['hidden_detail_count'] == 1



def test_final_review_gate_blocks_model_when_no_exception(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'DATA', tmp_path)
    monkeypatch.setattr(core, 'DB', tmp_path / 'review-gate.sqlite3')
    with core.connect() as con:
        con.execute("INSERT INTO sources VALUES (?,?,?,?,?,?,?,?)", ('s','x.pdf','h',str(tmp_path/'x.pdf'),'','','1','now'))
        con.execute("INSERT INTO devices VALUES (?,?,?,?,?)", ('d','Vendor','Family','SSD','s'))
        con.execute("""INSERT INTO extraction_runs
          (id,device_id,source_id,extraction_mode,schema_valid,model_calls,unresolved_evidence_json,review_required,review_queue_json,created_at)
          VALUES (?,?,?,?,?,?,?,?,?,?)""", ('r','d','s','agent_single_pass_generic',1,1,'[]',0,'[]','now'))
    called = {'n': 0}
    monkeypatch.setattr(ai, 'final_review', lambda *args, **kwargs: called.__setitem__('n', called['n'] + 1))
    client = TestClient(app)
    response = client.post('/api/devices/d/final-review')
    assert response.status_code == 200
    assert response.json()['overall_status'] == 'not_required'
    assert called['n'] == 0
    status = client.get('/api/devices/d/extraction-status').json()
    assert status['model_calls'] == 1
    assert status['review_required'] is False
