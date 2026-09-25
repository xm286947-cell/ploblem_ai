from pathlib import Path

HTML = Path(__file__).resolve().parents[1] / 'storage_life' / 'index.html'


def page():
    return HTML.read_text(encoding='utf-8')


def test_key_and_diagnostic_are_default_and_first():
    s = page()
    assert "familyMode='key'" in s
    assert '关键规格 + 诊断能力' in s
    assert s.index('关键规格 + 诊断能力') < s.index('分析结论')
    assert 'renderDiagnosticCapabilities' in s
    assert 'renderOtherSpecs' in s


def test_lifetime_markers_are_explicit():
    s = page()
    assert "return '寿命关键'" in s
    assert "return '寿命相关'" in s
    assert "'pe_cycles','retention'" in s
    assert "'tbw','dwpd','endurance_class'" in s


def test_model_confirmation_is_only_visible_in_diff_mode():
    s = page()
    assert "$('#modelCandidates').style.display=familyMode==='diff'?'block':'none'" in s
    assert '料号确认只在“料号差异”中处理，不进入关键规格展示。' in s


def test_one_click_confirm_only_pending_models():
    s = page()
    assert "currentModels.filter(m=>m.verify_status==='pending')" in s
    assert '一键确认待确认料号' in s
    assert "status:'confirmed'" in s
    assert '已驳回项保持不变' in s
