from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_root_run_log_is_always_present():
    p = ROOT / "RUN_LOG.txt"
    assert p.is_file()
    text = p.read_text(encoding="utf-8")
    assert "STORAGE_PRODUCT_TEST_FULL_V1.12" in text


def test_windows_launcher_creates_root_log_before_server_start():
    text = (ROOT / "run_windows.bat").read_text(encoding="utf-8")
    create_pos = text.find('>"%ROOT_LOG%" echo')
    start_pos = text.find('scripts\\windows_start.py')
    assert create_pos >= 0
    assert start_pos > create_pos
    assert 'copy /Y "%ROOT_LOG%" "%PACKAGE_ROOT%\\logs\\RUN_LOG_latest.txt"' in text


def test_user_has_one_click_log_viewer():
    p = ROOT / "查看日志.bat"
    assert p.is_file()
    text = p.read_text(encoding="utf-8")
    assert "RUN_LOG.txt" in text
    assert "notepad.exe" in text
