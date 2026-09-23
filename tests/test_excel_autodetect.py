from pathlib import Path
import json

from openpyxl import Workbook

from parser.excel_parser import ExcelParser


ROOT = Path(__file__).resolve().parents[1]


def _make_workbook(path: Path) -> None:
    wb = Workbook()
    cover = wb.active
    cover.title = "说明"
    cover["A1"] = "这是说明页"

    data = wb.create_sheet("识别重复发生-24-25年4个SPDT重大复盘对比库")
    headers = [
        "重大编号", "ITR单号 ", "考核年份", "考核月份",
        "IPMT", "SPDT", "质量代表", "责任部门（二级）", "问题描述"
    ]
    data.append(headers)
    data.append([
        "240202", "ITR20240129077", "2024年", "2月",
        "传动IPMT", "低压变频器SPDT", "", "", "问题一"
    ])
    data.append([
        "240303", "ITR20240306005", "2024年", "3月",
        "传动IPMT", "低压变频器SPDT", "潘欢", "", "问题二"
    ])
    wb.save(path)


def test_auto_detect_sheet_header_and_aliases(tmp_path):
    excel = tmp_path / "cases.xlsx"
    output = tmp_path / "raw_excel"
    _make_workbook(excel)

    parser = ExcelParser(ROOT / "config/field_mapping.yaml")
    records, summary = parser.parse(excel, output)

    assert summary.sheet_name == "识别重复发生-24-25年4个SPDT重大复盘对比库"
    assert summary.header_row == 1
    assert summary.total_rows == 2
    assert records[0]["mapped_fields"]["itr_id"] == "ITR20240129077"
    assert records[0]["mapped_fields"]["responsible_department_level2"] == ""
    assert records[0]["mapped_fields"]["original_description"] == "问题一"
    assert "责任部门(二级)" not in summary.missing_columns


def test_summary_contains_sheet_diagnostics(tmp_path):
    excel = tmp_path / "cases.xlsx"
    output = tmp_path / "raw_excel"
    _make_workbook(excel)

    parser = ExcelParser(ROOT / "config/field_mapping.yaml")
    _, summary = parser.parse(excel, output)

    names = [item["sheet_name"] for item in summary.sheet_diagnostics]
    assert "说明" in names
    assert "识别重复发生-24-25年4个SPDT重大复盘对比库" in names
    selected = next(
        item for item in summary.sheet_diagnostics
        if item["sheet_name"] == summary.sheet_name
    )
    assert selected["mapped_field_matches"] >= 6
