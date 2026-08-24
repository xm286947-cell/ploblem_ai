from pathlib import Path
from fastapi.testclient import TestClient
from openpyxl import Workbook
from quality_knowledge.web.app import create_app


def test_web_import_route_is_preview_before_commit_source_contract():
    src=(Path(__file__).parents[1]/'quality_knowledge/web/app.py').read_text(encoding='utf-8')
    assert "@app.post('/import/preview'" in src
    assert "@app.post('/import/confirm'" in src
    block=src[src.index("@app.post('/import',"):src.index("@app.get('/imports/{batch_id}'")]
    assert 'save_and_import(file, business_type)' not in block
    assert "TemplateResponse(request,'import_preview.html'" in block


def test_intake_template_requires_preview_then_confirm():
    root=Path(__file__).parents[1]/'quality_knowledge/web/templates'
    a=(root/'import.html').read_text(encoding='utf-8')
    b=(root/'import_preview.html').read_text(encoding='utf-8')
    assert 'action="/import/preview"' in a and '开始预检' in a
    assert 'action="/import/confirm"' in b and '确认正式导入' in b
    assert '尚未写入正式 Knowledge' in b


def test_intake_session_service_roundtrip(tmp_path):
    from quality_knowledge.services.intake_session_service import IntakeSessionService
    s=IntakeSessionService(tmp_path);m=s.create('x.xlsx',b'abc');assert s.get(m['intake_session_id'])['status']=='PREVIEW'
    s.mark_committed(m['intake_session_id'],'IMP-1');m2=s.get(m['intake_session_id']);assert m2['status']=='COMMITTED' and m2['batch_id']=='IMP-1'
