from pathlib import Path


def test_windows_launcher_is_rooted_to_its_own_package():
    root = Path(__file__).resolve().parents[1]
    launcher = (root / "run_windows.bat").read_text(encoding="utf-8")
    text = launcher
    assert 'for %%I in ("%~dp0.") do set "PACKAGE_ROOT=%%~fI"' in launcher
    assert 'cd /d "%PACKAGE_ROOT%"' in launcher
    assert '>"%ROOT_LOG%" echo' in launcher
    assert 'set "UNIFIED_AGENT_RUNTIME_ROOT=%PACKAGE_ROOT%\\vendor\\unified_agent_runtime"' in text
    assert 'set "STORAGE_STRICT_PACKAGE_PROVENANCE=1"' in text
    assert 'set "PYTHONHOME="' in text
    assert 'set "PYTHONPATH=%PACKAGE_ROOT%;%UNIFIED_AGENT_RUNTIME_ROOT%"' in text
    assert '"%PACKAGE_ROOT%\\.venv\\Scripts\\python.exe" "%PACKAGE_ROOT%\\scripts\\windows_start.py"' in text
    assert 'windows_e2e.py' not in text
    assert 'product_e2e.py' not in text


def test_windows_launcher_defaults_model_config_to_package_but_preserves_user_override():
    root = Path(__file__).resolve().parents[1]
    launcher = (root / "run_windows.bat").read_text(encoding="utf-8")
    assert 'if "%STORAGE_MODEL_CONFIG%"=="" (' in launcher
    assert 'set "STORAGE_MODEL_CONFIG=%PACKAGE_ROOT%\\config\\model.local.yaml"' in launcher
    assert 'set "STORAGE_MODEL_CONFIG_SOURCE=package-default"' in launcher
    assert 'set "STORAGE_MODEL_CONFIG_SOURCE=user"' in launcher
    assert launcher.count('STORAGE_MODEL_CONFIG=%PACKAGE_ROOT%\\config\\model.local.yaml') == 1
    assert 'RUN_LOG.txt' in launcher


def test_windows_e2e_has_executable_provenance_gate():
    root = Path(__file__).resolve().parents[1]
    text = (root / "scripts" / "windows_e2e.py").read_text(encoding="utf-8")
    for token in [
        "verify_process_provenance",
        "PROVENANCE_GATE_PROCESS_FAILED",
        "PROVENANCE_GATE_RUNTIME_FAILED",
        "PROVENANCE_GATE_MODEL_CONFIG_FAILED",
        "verify_effective_provenance",
        "PROVENANCE_GATE_EFFECTIVE_FAILED",
        "python_executable",
        "storage.emmc.parameter_extract",
        "agent_config:{agent_id}",
    ]:
        assert token in text
    assert 'env["PYTHONPATH"] = os.pathsep.join([str(ROOT), str(runtime_root)])' in text


def test_effective_config_records_actual_loaded_sources_without_secret():
    root = Path(__file__).resolve().parents[1]
    text = (root / "scripts" / "effective_runtime_config.py").read_text(encoding="utf-8")
    for token in [
        '"package_root"', '"working_dir"', '"python_executable"', '"runtime_module"',
        '"runtime_model_config"', '"agent_configs"', '"runtime_provider_adapter"',
        '"storage_runtime_bridge_module"', '"final_url"', '"api_key_present"',
    ]:
        assert token in text
    assert '"api_key":' not in text


def test_runtime_bridge_exposes_exact_storage_agent_config_paths():
    root = Path(__file__).resolve().parents[1]
    text = (root / "storage_life" / "runtime_bridge.py").read_text(encoding="utf-8")
    assert '"agent_configs": {' in text
    assert 'GENERIC_AGENT_ID: str(generic_agent_config)' in text
    assert 'EMMC_PARAMETER_AGENT_ID: str(emmc_agent_config)' in text
    assert '"runtime_module": str(runtime_module)' in text
    assert 'Runtime import source 不匹配' in text


def test_default_real_provider_is_internal_openai_compatible():
    root = Path(__file__).resolve().parents[1]
    text = (root / "config" / "model.local.yaml").read_text(encoding="utf-8")
    assert "base_url: http://127.0.0.1:8000/v1" in text


def test_strict_provenance_rejects_model_config_from_other_product_package(tmp_path):
    from scripts import windows_e2e

    foreign = tmp_path / "STORAGE_PRODUCT_TEST_FULL_V1.0" / "config" / "model.local.yaml"
    foreign.parent.mkdir(parents=True)
    foreign.write_text("active_model: qwen_prod\nmodels: {}\n", encoding="utf-8")
    tee = windows_e2e.Tee(tmp_path / "session.log", tmp_path / "latest.log")
    env = {
        "STORAGE_MODEL_CONFIG": str(foreign),
        "STORAGE_MODEL_CONFIG_SOURCE": "user",
        "STORAGE_STRICT_PACKAGE_PROVENANCE": "1",
    }
    import pytest

    with pytest.raises(RuntimeError, match="PROVENANCE_GATE_MODEL_CONFIG_FAILED"):
        windows_e2e.resolve_model_config(env, tee)
