from storage_life import core, document_pipeline


def test_extract_pdf_max_pages_limits_text_extraction(monkeypatch):
    class Page:
        images=[]
        def __init__(self, n): self.n=n
        def extract_text(self): return f'page-{self.n}'
    class Reader:
        def __init__(self, _): self.pages=[Page(i) for i in range(1, 11)]
    monkeypatch.setattr(core, 'PdfReader', Reader)
    pages=core.extract_pdf(b'%PDF', max_pages=3)
    assert [p[0] for p in pages] == [1,2,3]
    assert [p[1] for p in pages] == ['page-1','page-2','page-3']


def test_identify_route_declares_progressive_fast_path():
    from pathlib import Path
    source=Path('storage_life/app.py').read_text(encoding='utf-8')
    assert 'core.extract_pdf, data, 3' in source
    assert 'core.extract_pdf, data, 6' in source
    assert 'identify_mode' in source
    assert 'identify_input_chars' in source
    assert '0.70' in source


def test_markdown_parser_filters_table_pages_to_requested_pages():
    from pathlib import Path
    source=Path('storage_life/document_pipeline.py').read_text(encoding='utf-8')
    assert 'wanted_pages = {int(p[0]) for p in pages}' in source
    assert 'if idx not in wanted_pages' in source
