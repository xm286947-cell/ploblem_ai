"""P0: optional Repeat switch must not disable D01 at product startup."""
from __future__ import annotations
from pathlib import Path
import yaml
from scripts.major_mvp_product_start import build_app
from quality_knowledge.major_cases.runtime_provider import build_major_d01_provider
from runtime.config.errors import AgentConfigError, ConfigValidationError

def _local_model_config(tmp_path: Path, *, enabled: bool | None = None, extra: dict | None = None) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    cfg={"active_model": "qwen_prod", "models": {"qwen_prod": {
        "provider": "openai_compatible", "base_url": "http://127.0.0.1:1/v1",
        "api_key": "LOCAL_TEST_PLACEHOLDER_ONLY", "model": "local-test-model",
        "temperature": 0, "max_tokens": 8192,
    }}}
    if enabled is not None:
        cfg["repeat_decision_ai"]={"enabled":enabled}
    if extra is not None: cfg.update(extra)
    path=tmp_path/"approved-external-model.yaml"
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return path

def test_d01_model_config_without_repeat_flag_is_accepted(tmp_path: Path) -> None:
    assert callable(build_major_d01_provider(
        Path(__file__).resolve().parents[1], _local_model_config(tmp_path)
    ))

def test_explicit_on_and_off_m84_switch_do_not_disable_d01(tmp_path: Path, monkeypatch) -> None:
    for enabled in (False, True):
        config=_local_model_config(tmp_path/str(enabled), enabled=enabled)
        monkeypatch.setenv("MAJOR_MODEL_CONFIG", str(config))
        app=build_app(tmp_path/f"data-root-{enabled}")
        assert app.state.major_provider_status["configured"] is True
        assert app.state.repeat_risk_service.agent_analysis.decision_enabled is enabled

def test_unknown_model_config_key_keeps_strict_validation(tmp_path: Path) -> None:
    cfg=_local_model_config(tmp_path,enabled=True,extra={"unexpected_model_config_option":True})
    try: build_major_d01_provider(Path(__file__).resolve().parents[1],cfg)
    except (ConfigValidationError, AgentConfigError): pass
    else: raise AssertionError("Runtime must reject unknown top-level model fields")

def test_non_mapping_model_config_is_rejected(tmp_path: Path) -> None:
    path=tmp_path/"invalid-model.yaml"
    path.write_text("- invalid-model-profile\n",encoding="utf-8")
    try: build_major_d01_provider(Path(__file__).resolve().parents[1],path)
    except (ConfigValidationError, AgentConfigError): pass
    else: raise AssertionError("Major D01 must fail closed for non-mapping config")
