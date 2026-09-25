from __future__ import annotations

import io
import os
import sys
from pathlib import Path


def _gbk_stdout():
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="gbk", errors="strict", write_through=True)
    return raw, stream


def test_hotfix_launcher_forces_utf8_before_python():
    root = Path(__file__).resolve().parents[1]
    text = (root / "run_windows.bat").read_text(encoding="utf-8")
    assert "chcp 65001" in text
    assert 'set "PYTHONUTF8=1"' in text
    assert 'set "PYTHONIOENCODING=utf-8"' in text
    assert text.index("chcp 65001") < text.index(".venv\\Scripts\\python.exe")


def test_runtime_provider_trace_survives_gbk_console(monkeypatch, tmp_path):
    root = Path(__file__).resolve().parents[1]
    runtime_root = root / "vendor" / "unified_agent_runtime"
    sys.path.insert(0, str(runtime_root))
    try:
        from runtime.providers import openai_compatible as provider

        trace = tmp_path / "provider_runtime.log"
        monkeypatch.setenv("RUNTIME_PROVIDER_TRACE_FILE", str(trace))
        _raw, stream = _gbk_stdout()
        monkeypatch.setattr(sys, "stdout", stream)

        provider._write_provider_trace(
            {
                "phase": "request",
                "diagnostic_text": "datasheet 10µA ✓ Ω",
            }
        )
        stream.flush()
        saved = trace.read_text(encoding="utf-8")
        assert "10µA ✓ Ω" in saved
    finally:
        if str(runtime_root) in sys.path:
            sys.path.remove(str(runtime_root))


def test_windows_e2e_tee_survives_gbk_console(monkeypatch, tmp_path):
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "scripts"))
    try:
        import windows_e2e

        _raw, stream = _gbk_stdout()
        monkeypatch.setattr(sys, "stdout", stream)
        tee = windows_e2e.Tee(tmp_path / "session.log", tmp_path / "latest.log")
        tee.write("PDF text: 10µA ✓ Ω\n")
        stream.flush()
        assert "10µA ✓ Ω" in (tmp_path / "latest.log").read_text(encoding="utf-8")
    finally:
        if str(root / "scripts") in sys.path:
            sys.path.remove(str(root / "scripts"))
