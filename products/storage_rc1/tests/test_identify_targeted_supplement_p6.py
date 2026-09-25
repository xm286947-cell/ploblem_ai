from pathlib import Path
from storage_life import core


def test_extract_pdf_pages_selects_non_contiguous_pages(monkeypatch):
    class Page:
        images=[]
        def __init__(self, n): self.n=n
        def extract_text(self): return f'page-{self.n}'
    class Reader:
        def __init__(self, _): self.pages=[Page(i) for i in range(1, 66)]
    monkeypatch.setattr(core, 'PdfReader', Reader)
    pages=core.extract_pdf_pages(b'%PDF', [1,2,3,5,4,5])
    assert [p[0] for p in pages] == [1,2,3,4,5]
    assert pages[-1][1] == 'page-5'


def test_p6_declares_toc_targeting_and_bounded_fallback():
    source=Path('storage_life/app.py').read_text(encoding='utf-8')
    assert '_toc_target_pages' in source
    assert 'GENERAL\\s+DESCRIPTIONS?' in source
    assert 'FEATURES?' in source
    assert 'core.extract_pdf_pages, data, wanted' in source
    assert 'targeted_toc_pages' in source
    assert 'expanded_6_pages_fallback' in source
    assert 'identify_targeted_pages' in source
    assert 'page_no <= 12' in source


def test_basic_identity_does_not_target_ordering_information():
    source=Path('storage_life/app.py').read_text(encoding='utf-8')
    block=source[source.index('def _toc_target_pages'):source.index('targeted_pages = _toc_target_pages')]
    assert 'ORDERING' not in block
    assert 'VALID PART' not in block
