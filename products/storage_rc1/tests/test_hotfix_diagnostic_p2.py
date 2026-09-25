from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_windows_e2e(root: Path):
    path = root / "scripts" / "windows_e2e.py"
    spec = importlib.util.spec_from_file_location("windows_e2e_p2", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_diagnostic_bundle_has_no_removed_direct_probe_reference(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    module = _load_windows_e2e(root)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    logs = tmp_path / "logs"
    release = tmp_path / "release"
    logs.mkdir(parents=True)
    release.mkdir(parents=True)
    session = logs / "session.log"
    session.write_text("PRODUCT E2E PASS\n", encoding="utf-8")
    (logs / "effective_runtime_config.json").write_text("{}\n", encoding="utf-8")
    (logs / "provider_runtime.log").write_text('{"phase": "response", "status": 200}\n', encoding="utf-8")
    (release / "storage_app.log").write_text("app ok\n", encoding="utf-8")

    out = module.collect_diagnostics(session, outcome="PASS")
    text = out.read_text(encoding="utf-8")
    assert "outcome=PASS" in text
    assert "RUNTIME PROVIDER REQUEST TRACE" in text
    assert "DIRECT NETWORK / HTTP PROTOCOL PROBE" not in text


def test_windows_real_path_still_skips_direct_provider_probe():
    root = Path(__file__).resolve().parents[1]
    text = (root / "scripts" / "windows_e2e.py").read_text(encoding="utf-8")
    assert "direct_probe=SKIPPED; network_owner=Unified Agent Runtime" in text
    assert "DIRECT NETWORK / HTTP PROTOCOL PROBE" not in text
