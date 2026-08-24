from pathlib import Path
from fastapi.testclient import TestClient
from quality_knowledge.web.app import create_app


def test_global_shell_and_cache_busted_css(tmp_path):
    client=TestClient(create_app(tmp_path/'q.db'))
    html=client.get('/import').text
    assert 'class="side-nav"' in html
    assert 'INOVANCE' in html and 'Quality Issue Analysis' in html
    assert 'app.css?v=ued2' in html
    assert '当前后端' not in html and 'UI 不伪造' not in html


def test_ued_v2_css_has_desktop_sidebar_and_cross_browser_guards():
    css=Path('quality_knowledge/web/static/app.css').read_text(encoding='utf-8')
    for required in [
        'position:fixed!important', 'width:220px', 'background:var(--ued-side)!important',
        '.nav-item.active{background:#fff!important', 'scrollbar-width:thin',
        '-webkit-overflow-scrolling:touch', 'button::-moz-focus-inner',
        '@supports not (overflow-wrap:anywhere)', '@media(max-width:980px)'
    ]:
        assert required in css


def test_import_uses_ued_r5_information_hierarchy(tmp_path):
    client=TestClient(create_app(tmp_path/'q.db'))
    html=client.get('/import').text
    for text in ['数据接入与字段映射','选择文件','检查 Mapping','正式导入','文件与业务识别','导入前检查']:
        assert text in html
