from io import BytesIO
from pathlib import Path

import pytest
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

from storage_life import core, ai, document_pipeline, templates


def make_identity_pdf(extra=''):
    buf = BytesIO()
    doc = SimpleDocTemplate(buf)
    styles = getSampleStyleSheet()
    table = Table([
        ['Parameter', 'Typical', 'Max', 'Unit'],
        ['Page Program Time', '300', '600', 'us'],
        ['P/E Cycle', '100K', '', 'cycles'],
    ])
    table.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 0.5, colors.black),
        ('BACKGROUND', (0,0), (-1,0), colors.lightgrey),
    ]))
    story = [
        Paragraph('GigaDevice GD5F1GQ5UExxG DATASHEET', styles['Title']),
        Paragraph('Document No. DS-00888', styles['Normal']),
        Paragraph('Rev1.5', styles['Normal']),
        Paragraph('2023-02-15', styles['Normal']),
        Paragraph('Released', styles['Normal']),
        Paragraph(extra, styles['Normal']), Spacer(1, 12), table,
    ]
    doc.build(story)
    return buf.getvalue()


def test_pdf_to_markdown_preserves_page_and_table_structure():
    data = make_identity_pdf()
    pages = core.extract_pdf(data)
    md, md_pages, stats = document_pipeline.build_markdown(data, pages)
    assert '# Page 1' in md
    assert 'Page Program Time' in md
    assert stats['parser_version'] == 'md-v1'
    assert stats['table_count'] >= 1
    assert md_pages[0][0] == 1 and md_pages[0][2].startswith('markdown_')


def test_document_identity_is_persisted_and_duplicate_version_blocked(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'DATA', tmp_path)
    monkeypatch.setattr(core, 'DB', tmp_path / 'v080.sqlite3')
    monkeypatch.setattr(ai, 'configured', lambda: False)
    data = make_identity_pdf()
    result = core.import_document('gd5f.pdf', data, 'GigaDevice', 'GD5F1GQ5UExxG', 'NAND Flash')
    identity = core.get_document_identity(result['device_id'])
    assert identity['document_number'] == 'DS-00888'
    assert '1.5' in identity['revision']
    assert identity['revision_date'] == '2023-02-15'
    assert identity['official_latest_status'] == 'matches_catalog_latest'
    assert identity['official_latest_revision'] == 'Rev1.5'
    parsed = core.get_parsed_document(result['device_id'], include_markdown=True)
    assert parsed['parser_version'] == 'md-v1'
    assert '# Page 1' in parsed['markdown']
    with pytest.raises(core.DuplicateDocumentError):
        core.import_document('same.pdf', data, 'GigaDevice', 'GD5F1GQ5UExxG', 'NAND Flash')


def test_same_logical_document_version_different_bytes_is_duplicate(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'DATA', tmp_path)
    monkeypatch.setattr(core, 'DB', tmp_path / 'identity.sqlite3')
    monkeypatch.setattr(ai, 'configured', lambda: False)
    a = make_identity_pdf('copy A')
    b = make_identity_pdf('copy B')
    core.import_document('a.pdf', a, 'GigaDevice', 'GD5F1GQ5UExxG', 'NAND Flash')
    with pytest.raises(core.DuplicateDocumentError):
        core.import_document('b.pdf', b, 'GigaDevice', 'GD5F1GQ5UExxG', 'NAND Flash')


def test_reviewed_spec_layer_maps_legacy_candidates_to_rc2_semantics(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'DATA', tmp_path)
    monkeypatch.setattr(core, 'DB', tmp_path / 'reviewed.sqlite3')
    with core.connect() as con:
        con.execute("INSERT INTO sources VALUES (?,?,?,?,?,?,?,?)", ('s','x.pdf','h',str(tmp_path/'x.pdf'),'','','1','now'))
        con.execute("INSERT INTO devices VALUES (?,?,?,?,?)", ('d','GigaDevice','GD5F','NAND Flash','s'))
        vals = [
            ('c1','nand_type','SLC','','','SLC NAND Flash'),
            ('c2','ecc_requirement','4 bits/528 bytes','','','ECC capability 4 bits/528 bytes'),
            ('c3','bad_block_requirement','1004 blocks','','minimum number of valid blocks','1004 blocks minimum number of valid blocks'),
            ('c4','bad_block_requirement','00h','','bad block mark','Bad Block Mark 00h'),
        ]
        for cid, canon, value, unit, condition, quote in vals:
            con.execute("""INSERT INTO candidates(id,device_id,canonical_name,parameter_name,ai_value,ai_unit,condition,scope,source_page,source_text,confidence,extraction_method)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (cid,'d',canon,canon,value,unit,condition,'product family',1,quote,.9,'agent_text'))
            con.execute("INSERT INTO candidate_evidence VALUES (?,?,?,?,?,?,?,?)", ('e'+cid,cid,1,'',quote,.9,'agent_text','product family'))
    out = core.rebuild_reviewed_specifications('d')
    by = {x['canonical_name']: x for x in out}
    assert by['cell_type']['display'] == '单层单元（SLC）'
    assert by['ecc_capability']['display'].startswith('4 bits/528 bytes')
    assert by['minimum_valid_blocks']['display'].startswith('1004 blocks')
    assert by['bad_block_mark']['display'] == '00h'
    assert by['ecc_capability']['priority'] == 'P1'


def test_analysis_templates_focus_on_lifetime_and_diagnostics():
    nand = set(templates.analysis_fields_for('NAND Flash'))
    emmc = set(templates.analysis_fields_for('eMMC'))
    ssd = set(templates.analysis_fields_for('SSD'))
    assert {'pe_cycles','retention','ecc_capability','program_fail','erase_fail'} <= nand
    assert {'life_time_a','life_time_b','pre_eol','ext_csd_health_report'} <= emmc
    assert {'tbw','dwpd','smart_health','percentage_used','data_units_written'} <= ssd
    assert 'interface' not in nand and 'sequential_read' not in ssd
