from __future__ import annotations

import threading
from pathlib import Path
import inspect

from scripts.major_mvp_product_start import build_app, main as start_main
from quality_knowledge.repeat_risk.agent_analysis import RepeatAgentAnalysisService
from quality_knowledge.web.repeat_risk_integration import RepeatWebFacade
from tools.openai_mock.server import Behavior, create_server


def test_repeat_runtime_data_root_isolated(tmp_path: Path) -> None:
    project = tmp_path / "readonly-product"
    data_root = tmp_path / "writable-data"
    project.mkdir()
    service = RepeatAgentAnalysisService(project, runtime_data_root=data_root)
    assert service.report_root == (data_root / "repeat_reports").resolve()
    # Runtime is intentionally not invoked: provider setup belongs to the
    # existing Runtime contract tests.  This gate fixes the persistence root.
    assert service.runtime_data_root == data_root.resolve()
    assert not (project / "data").exists()


def test_m84_external_config_controls_optional_switch(tmp_path: Path) -> None:
    project = tmp_path / "product"
    project.mkdir()
    packaged = project / "config" / "model.yaml"
    packaged.parent.mkdir(parents=True)
    packaged.write_text("repeat_decision_ai:\n  enabled: false\n", encoding="utf-8")

    external = tmp_path / "approved-runtime.yaml"
    external.write_text("repeat_decision_ai:\n  enabled: true\n", encoding="utf-8")

    default_service = RepeatAgentAnalysisService(project)
    enabled_service = RepeatAgentAnalysisService(project, model_config_path=external)
    assert default_service.decision_enabled is False
    assert enabled_service.decision_enabled is True


def test_repeat_facade_accepts_runtime_data_root_contract() -> None:
    # Keep this as a signature-level regression so product composition cannot
    # silently drop the writable root again.
    assert "runtime_data_root" in inspect.signature(RepeatWebFacade.from_project).parameters


def test_standard_launcher_keeps_m84_off_and_external_config_invokes_runtime(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("MAJOR_MODEL_CONFIG", raising=False)
    assert start_main(["--check"]) == 0
    default_app = build_app(tmp_path / "default-data")
    default_service = default_app.state.repeat_risk_service.agent_analysis
    assert default_service.decision_enabled is False
    disabled, disabled_execution = default_service._recommendation(
        {"query_id": "Q-OFF", "case_id": "CASE-OFF"}, {}, {}
    )
    assert disabled["status"] == "DISABLED"
    assert disabled_execution["provider_calls"] == 0
    assert default_service.runtime is None

    server = create_server("127.0.0.1", 0)
    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.01},
        daemon=True,
    )
    thread.start()
    try:
        host, port = server.server_address
        external_config = tmp_path / "approved-model-config.yaml"
        external_config.write_text(
            "active_model: repeat_mock\n"
            "models:\n"
            "  repeat_mock:\n"
            "    provider: openai_compatible\n"
            f"    base_url: http://{host}:{port}/v1\n"
            "    api_key: local-test-only-secret\n"
            "    model: repeat-mock-model\n"
            "    temperature: 0\n"
            "    max_tokens: 4096\n"
            "repeat_decision_ai:\n"
            "  enabled: true\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("MAJOR_MODEL_CONFIG", str(external_config))
        assert start_main(["--check"]) == 0
        configured_app = build_app(tmp_path / "configured-data")
        service = configured_app.state.repeat_risk_service.agent_analysis
        assert service.decision_enabled is True

        server.state.configure(
            "default",
            {
                "decision": "LIKELY_REPEAT",
                "confidence": 0.88,
                "decision_reason": "Runtime test recommendation.",
                "evidence_chain": [],
                "key_differences": [],
                "validation_required": [],
                "risks": [],
                "recommended_actions": [],
            },
            Behavior(),
        )
        recommendation, execution = service._recommendation(
            {"query_id": "Q-ON", "case_id": "CASE-ON"},
            {"analysis_status": "SUCCESS"},
            {"analysis_status": "SUCCESS"},
        )
        assert recommendation["status"] == "SUCCESS"
        assert recommendation["decision"] == "LIKELY_REPEAT"
        assert execution["status"] == "SUCCESS"
        assert execution["provider_calls"] == 1
        assert server.state.counters()["default"] == 1
        assert "major_issue.repeat_case" in service._resolved
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
