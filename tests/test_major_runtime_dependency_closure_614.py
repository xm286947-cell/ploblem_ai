"""Guard product Candidate runtime dependencies, independent of CI test extras (#614)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_binary_xls_reader_declared_in_runtime_requirements():
    runtime = (ROOT / "requirements-major-mvp-product.txt").read_text("utf-8")
    tokens = [line.strip().split("#", 1)[0].strip() for line in runtime.splitlines()]
    assert "xlrd==2.0.2" in tokens
    parser = (ROOT / "parser/excel_parser.py").read_text("utf-8")
    assert "import xlrd" in parser
    assert "xlrd.open_workbook(" in parser


def test_candidate_and_macos_launcher_consume_runtime_requirements():
    build = (ROOT / "scripts/build_major_mvp_product_candidate.py").read_text("utf-8")
    mac = (ROOT / "START_MAJOR_MVP.command").read_text("utf-8")
    shell = (ROOT / "START_MAJOR_MVP.sh").read_text("utf-8")
    assert '"requirements-major-mvp-product.txt"' in build
    assert 'START_MAJOR_MVP.sh' in mac
    assert 'requirements-major-mvp-product.txt' in shell
    assert '.venv/bin/python' in shell
