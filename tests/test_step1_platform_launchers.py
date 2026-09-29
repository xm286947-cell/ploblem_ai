from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_step1_launchers_keep_one_legacy_business_entrypoint():
    windows = (ROOT / "start_quality_capability_p1.bat").read_text(encoding="utf-8")
    macos = (ROOT / "start_quality_capability_p1.command").read_text(encoding="utf-8")

    for launcher in (windows, macos):
        assert "main.py" in launcher
        assert "knowledge-web" in launcher
        assert "LEGACY_QUALITY_ISSUE_DB_PATH" in launcher
        assert "quality_issue_v1.db" in launcher

    assert "START_OVERALL_R2" not in windows
    assert "START_OVERALL_R2" not in macos


def test_step1_launchers_prefer_project_venv_then_python_311():
    windows = (ROOT / "start_quality_capability_p1.bat").read_text(encoding="utf-8")
    macos = (ROOT / "start_quality_capability_p1.command").read_text(encoding="utf-8")

    assert windows.index(".venv\\Scripts\\python.exe") < windows.index("py -3.11")
    assert "python main.py" not in windows
    assert macos.index(".venv/bin/python") < macos.index("python3.11")
    assert "python3 main.py" not in macos


def test_macos_launcher_is_double_click_executable():
    launcher = ROOT / "start_quality_capability_p1.command"
    assert launcher.read_text(encoding="utf-8").startswith("#!/bin/zsh")
    assert launcher.stat().st_mode & 0o111
