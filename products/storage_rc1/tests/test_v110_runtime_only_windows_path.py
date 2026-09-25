from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_windows_real_e2e_path_has_no_direct_provider_probe():
    text = (ROOT / "scripts" / "windows_e2e.py").read_text(encoding="utf-8")
    assert '"scripts/provider_probe.py"' not in text
    assert 'direct_probe=SKIPPED; network_owner=Unified Agent Runtime' in text


def test_normal_windows_launcher_declares_runtime_as_network_owner_without_e2e():
    text = (ROOT / "run_windows.bat").read_text(encoding="utf-8")
    assert 'STORAGE_LIFE_EXECUTION_MODE=runtime' in text
    assert 'NETWORK_OWNER=UNIFIED_AGENT_RUNTIME_ONLY' in text
    assert 'windows_start.py' in text
    assert 'windows_e2e.py' not in text
    assert 'product_e2e.py' not in text
    assert 'PRODUCT_E2E=NOT_RUN' in text
    assert 'MOCK=NOT_RUN' in text
    assert 'FAULT_TESTS=NOT_RUN' in text


def test_selftest_is_explicit_separate_entry():
    selftest = (ROOT / "selftest_windows.bat").read_text(encoding="utf-8")
    assert 'windows_e2e.py" --mode real' in selftest
    assert 'EXPLICIT SELFTEST' in selftest
    for name in ("run_product_test.bat", "start_test.bat"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert 'selftest_windows.bat' in text


def test_real_agent_and_mock_ports_are_separated():
    model = (ROOT / "config" / "model.local.yaml").read_text(encoding="utf-8")
    e2e = (ROOT / "scripts" / "windows_e2e.py").read_text(encoding="utf-8")
    assert 'base_url: http://127.0.0.1:8000/v1' in model
    assert 'http://127.0.0.1:18000/__mock__/health' in e2e
    assert 'http://127.0.0.1:18001/v1' in e2e


def test_storage_runtime_mode_cannot_fall_through_to_direct_provider_call():
    text = (ROOT / "storage_life" / "ai.py").read_text(encoding="utf-8")
    assert 'if _execution_mode() == "runtime":' in text
    assert 'return runtime_bridge.call_json(instructions, payload, schema)' in text
