from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from quality_knowledge.web.overall_runtime_control import (
    ConfigRevisionRequest,
    OverallRuntimeControlPlane,
)


ROOT = Path(__file__).resolve().parents[1]


def _launcher():
    path = ROOT / "scripts" / "overall_r2_windows_start.py"
    spec = importlib.util.spec_from_file_location("r2_hotfix_launcher", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_hotfix_289_formal_navigation_uses_mature_workbenches():
    shell = (ROOT / "quality_knowledge/web/overall_shell.py").read_text(encoding="utf-8")
    common = (ROOT / "quality_knowledge/web/templates/p0_issues.html").read_text(encoding="utf-8")
    expected = (
        "/itr/recovery-workbench",
        "/itr/resolution-workbench",
        "/software-assessment",
        "/missed-test-analysis",
    )
    for route in expected:
        assert route in shell
        assert f'href="{route}"' in common
    assert '"path": "/p0/itr-recovery"' not in shell
    assert '"path": "/p0/itr-resolution"' not in shell
    assert '"path": "/p0/software-assessment"' not in shell
    assert '"path": "/p0/missed-test-analysis"' not in shell


def test_hotfix_288_p08_restores_visible_flow_feedback_and_scalar_projection():
    html = (ROOT / "products/storage_rc1/storage_life/index.html").read_text(encoding="utf-8")
    assert "goldenANextAction" in html
    assert "参数识别正在运行，请保持页面打开" in html
    assert "Coverage → Review" in html
    assert "下一步：进入参数 Review 并核对 Evidence" in html
    assert "下一步：修正输入/环境后" in html
    assert "function identityValue(x)" in html
    assert "Array.isArray(x)" in html
    assert "typeof x==='object'" in html
    assert "(r.models||[]).map(identityValue)" in html


def test_hotfix_290_secretref_presence_and_restart_signal(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("custom_base", "https://provider.invalid/v1")
    monkeypatch.setenv("acca1", "SECRET_MUST_NOT_RENDER")
    control = OverallRuntimeControlPlane(
        project_root=ROOT,
        state_root=tmp_path / "runtime-control",
        runtime_dbs={},
    )
    revision = control.create_revision(
        ConfigRevisionRequest(
            active_model="manual_validation",
            models={
                "manual_validation": {
                    "provider": "openai_compatible",
                    "base_url_env": "custom_base",
                    "api_key_env": "acca1",
                    "model": "qwen3.8-max",
                    "temperature": 0,
                    "max_tokens": 8192,
                }
            },
            note="manual validation env-ref test",
        ),
        actor="pytest",
    )
    control.activate_revision(revision["revision_id"], actor="pytest")
    monkeypatch.setenv("OVERALL_RUNTIME_MODEL_CONFIG", revision["path"])

    effective = control.effective_config()
    model = effective["active_model_effective"]
    assert effective["active_revision"] == revision["revision_id"]
    assert effective["active_model"] == "manual_validation"
    assert effective["restart_required"] is False
    assert model["base_url_env"] == "custom_base"
    assert model["base_url_present"] is True
    assert model["api_key_env"] == "acca1"
    assert model["api_key_present"] is True
    assert model["process_env_scope"] == "CURRENT_PROCESS_ENV"

    diagnostics = control.diagnostics()
    operability = diagnostics["runtime_operability"]
    assert operability["model_ref"] == "manual_validation"
    assert operability["api_key_env_ref"] == "acca1"
    assert operability["api_key_present"] is True
    assert operability["base_url_present"] is True
    assert operability["restart_required"] is False

    serialized = json.dumps(
        {"effective": effective, "diagnostics": diagnostics},
        ensure_ascii=False,
    )
    assert "SECRET_MUST_NOT_RENDER" not in serialized


def test_hotfix_290_start_preflight_checks_actual_child_process_env(tmp_path: Path):
    launcher = _launcher()
    package_root = tmp_path / "package"
    config_dir = package_root / "config" / "runtime"
    config_dir.mkdir(parents=True)
    (config_dir / "model.yaml").write_text(
        """
active_model: manual_validation
models:
  manual_validation:
    provider: openai_compatible
    base_url_env: custom_base
    api_key_env: acca1
    model: qwen3.8-max
    temperature: 0
    max_tokens: 8192
""".strip(),
        encoding="utf-8",
    )
    control_root = tmp_path / "control"
    control_root.mkdir()

    missing_env = {}
    with pytest.raises(RuntimeError, match="PROVIDER_ENV_PREFLIGHT_FAILED") as exc:
        launcher._runtime_provider_preflight(
            package_root,
            control_root,
            missing_env,
        )
    message = str(exc.value)
    assert "custom_base" in message
    assert "acca1" in message
    assert "close/reopen the terminal" in message

    ready = launcher._runtime_provider_preflight(
        package_root,
        control_root,
        {
            "custom_base": "https://provider.invalid/v1",
            "acca1": "SECRET_MUST_NOT_PRINT",
        },
    )
    assert ready["model_ref"] == "manual_validation"
    assert ready["base_url_env_ref"] == "custom_base"
    assert ready["base_url_present"] is True
    assert ready["api_key_env_ref"] == "acca1"
    assert ready["api_key_present"] is True
    assert ready["process_env_scope"] == "START_PROCESS_INHERITED_ENV"
    assert ready["restart_required"] is False
