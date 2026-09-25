from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_p61_separates_part_number_page_plan_from_basic_identity():
    source = (ROOT / 'storage_life' / 'app.py').read_text(encoding='utf-8')
    assert 'ORDERING\\s+INFORMATION' in source
    assert 'VALID\\s+PART\\s+NUMBERS' in source
    assert 'model_targeted_pages' in source
    assert 'core.extract_pdf_pages, data, model_pages[:4]' in source


def test_p61_vendor_prefix_is_candidate_not_formal_evidence():
    source = (ROOT / 'storage_life' / 'app.py').read_text(encoding='utf-8')
    assert 'configured_model_prefix_candidate' in source
    ui = (ROOT / 'storage_life' / 'index.html').read_text(encoding='utf-8')
    assert '型号前缀候选，需人工确认' in ui


def test_p61_final_review_distinguishes_blocked_not_required_and_failure():
    source = (ROOT / 'storage_life' / 'app.py').read_text(encoding='utf-8')
    assert '"overall_status": "blocked"' in source
    assert '"overall_status": "not_required"' in source
    assert '料号确认属于 Final Review 前置完整性 Gate' in source
    ui = (ROOT / 'storage_life' / 'index.html').read_text(encoding='utf-8')
    assert 'Final Review：无需执行' in ui
    assert 'Final Review：等待前置确认' in ui
