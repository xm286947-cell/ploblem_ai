from pathlib import Path
import json
from builder.m71_query_runner import run_m71_query


def test_query_parser_normal(tmp_path: Path):
    root = tmp_path / "project"
    import shutil
    shutil.copytree(Path(__file__).resolve().parents[1], root)
    result = run_m71_query(root, str(root / "tests/samples/query_normal.xlsx"), overwrite=True)
    assert result["total"] == 2
    assert result["failed"] == 0
    data = json.loads((root / "knowledge/raw_query/QUERY001.json").read_text(encoding="utf-8"))
    assert data["mapped_fields"]["cause_level4"] == "CAN接收缓存"
    assert data["excel_row"] == 2
    assert data["parser_version"] == "2.0.0-m7.1"


def test_query_parser_double_header(tmp_path: Path):
    import shutil
    root = tmp_path / "project"; shutil.copytree(Path(__file__).resolve().parents[1], root)
    result = run_m71_query(root, str(root / "tests/samples/query_double_header.xlsx"), overwrite=True)
    assert result["header_rows"] == 2
    assert result["total"] == 1
    assert (root / "knowledge/raw_query/Q-DH-1.json").exists()


def test_query_parser_missing_required_isolated(tmp_path: Path):
    import shutil
    root = tmp_path / "project"; shutil.copytree(Path(__file__).resolve().parents[1], root)
    result = run_m71_query(root, str(root / "tests/samples/query_missing_required.xlsx"), overwrite=True)
    assert result["total"] == 3
    assert result["failed"] == 2
    assert result["success"] + result["partial_success"] == 1
    assert (root / "knowledge/raw_query/Q-OK.json").exists()
    assert len(list((root / "knowledge/raw_query").glob("FAILED-ROW-*.json"))) == 2


def test_query_parser_auto_select_sheet(tmp_path: Path):
    import shutil
    root = tmp_path / "project"; shutil.copytree(Path(__file__).resolve().parents[1], root)
    result = run_m71_query(root, str(root / "tests/samples/query_multi_sheet.xlsx"), overwrite=True)
    assert result["sheet_name"] == "数据"
    assert (root / "knowledge/raw_query/Q-MS-1.json").exists()
