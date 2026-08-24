from pathlib import Path
from openpyxl import Workbook

from parser.excel_parser import ExcelParser

ROOT = Path(__file__).resolve().parents[1]


def test_parser_reads_multiple_columns_without_trusting_max_column(tmp_path):
    excel = tmp_path / "cases.xlsx"
    output = tmp_path / "raw_excel"

    wb = Workbook()
    ws = wb.active
    ws.title = "数据"
    ws.append([
        "重大编号", "ITR单号", "考核年份", "考核月份",
        "IPMT", "SPDT", "质量代表", "责任部门（二级）", "问题描述"
    ])
    ws.append([
        "240202", "ITR20240129077", "2024年", "2月",
        "传动IPMT", "低压变频器SPDT", "", "", "问题一"
    ])
    wb.save(excel)

    parser = ExcelParser(ROOT / "config/field_mapping.yaml")
    records, summary = parser.parse(excel, output)

    assert summary.total_rows == 1
    assert len(summary.headers) >= 9
    assert records[0]["mapped_fields"]["itr_id"] == "ITR20240129077"
    assert records[0]["mapped_fields"]["original_description"] == "问题一"
