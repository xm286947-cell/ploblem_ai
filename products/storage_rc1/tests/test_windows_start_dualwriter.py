from __future__ import annotations

import io
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("windows_start", ROOT / "scripts" / "windows_start.py")
MOD = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MOD)


def test_dual_writer_exposes_uvicorn_stream_api():
    primary = io.StringIO()
    mirror = io.StringIO()
    writer = MOD.DualWriter(primary, mirror)
    assert writer.isatty() is False
    assert writer.writable() is True
    assert writer.encoding == "utf-8"
    assert writer.write("hello") == 5
    writer.flush()
    assert primary.getvalue() == "hello"
    assert mirror.getvalue() == "hello"
